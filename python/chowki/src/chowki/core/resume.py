"""Warm resume engine with state patching (ADR-004).

Warning (R4):
    Every side effect in a Chowki workflow must live inside a `@chowki.step`.
    Because `resume()` re-executes the workflow function body from the top,
    any side effect outside a `@chowki.step` will be re-executed on warm resume.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import structlog
from msgspec.structs import replace as msgspec_replace

from chowki.config import ChowkiEngine, get_engine
from chowki.errors import (
    ChowkiStateError,
    ExpiredResumeToken,
    HumanRejectedError,
    InvalidResumeToken,
    ReplayedNonceError,
    WorkflowPaused,
)
from chowki.hitl.audit import AuditLog, build_audit_record, redact_patch
from chowki.hitl.tokens import ResumeClaims
from chowki.state.canonical import content_hash
from chowki.state.delta import Patch, apply_patch
from chowki.types import Decision, JSONObject, RunRecord, RunStatus


@dataclass(frozen=True, slots=True)
class ResumeResult:
    run_id: str
    decision: Decision
    value: object
    state_hash_before: str
    state_hash_after: str


def _persist_state_of_record(engine: ChowkiEngine, run: RunRecord, state: dict[str, Any]) -> None:
    """Write the decided state to storage as the state the run continues from.

    Dropping the pipeline first makes the write a BASE, so `snapshots_for_resume` starts
    *at* the decided state and every later delta chains onto it. Without this, the
    snapshots the decision superseded stay the run's state of record, and a later gate --
    or a fresh process -- would load pre-edit values back.

    The index is a fresh one above every snapshot the run already has, never the pause
    boundary's: the re-execution replays the body from the top, so a step that runs again
    before the gate (a non-memoised step, or one whose arguments changed) writes its own
    snapshot, and `_open_run` starts that execution's indices above this base. Reusing the
    boundary index instead let such a write land *under* the base, leaving the deltas
    after it diffed against a document the decision had replaced.
    """
    stored_max = engine.storage.max_snapshot_index(run.run_id)
    next_index = stored_max if stored_max is not None else -1
    engine.drop_pipeline(run.run_id)
    engine.pipeline_for(run.run_id).snapshot(
        state,
        run_id=run.run_id,
        workflow=run.workflow,
        step_index=next_index + 1,
    )


def _invoke_workflow(workflow_fn: Callable[..., Any], run_id: str) -> Any:
    try:
        return workflow_fn(run_id=run_id)
    except TypeError as exc:
        if exc.__traceback__ is not None and exc.__traceback__.tb_next is not None:
            raise
        if "unexpected keyword argument" in str(exc) or "run_id" in str(exc):
            return workflow_fn()
        raise


def _confirm_gateway(
    engine: ChowkiEngine, run_id: str, decision: Decision, actor: JSONObject | None
) -> None:
    gateway = engine.gateway
    if gateway is not None:
        handle = engine.storage.get_gateway_handle(run_id)
        if handle is not None:
            try:
                gateway.confirm(handle, decision, actor=actor)
            except Exception:
                logger = structlog.get_logger()
                logger.exception("chowki_gateway_confirm_failed", run_id=run_id)


def resume(
    *,
    run_id: str,
    token: str,
    decision: Decision,
    workflow_fn: Callable[..., Any],
    engine: ChowkiEngine | None = None,
    patch: Patch | None = None,
    actor: JSONObject | None = None,
    note: str | None = None,
) -> ResumeResult:
    """Resume a paused workflow run with optional state patching and human decision.

    Warning (R4):
        Every side effect in a Chowki workflow must live inside a `@chowki.step`.
        Because `resume()` re-executes the workflow function body from the top,
        any side effect outside a `@chowki.step` will be re-executed on warm resume.

    Note (Phase 2):
        Workflow registry integration will allow resuming workflows by string name
        rather than requiring explicit `workflow_fn` function reference.
    """
    eff_engine = engine or get_engine()

    claims: ResumeClaims | None = None
    token_exc: Exception | None = None
    try:
        c = eff_engine.tokens.verify(token, action=decision.value)
        if c.run_id != run_id:
            raise InvalidResumeToken(f"token was issued for run {c.run_id!r}, not {run_id!r}")
        claims = c
    except (ReplayedNonceError, ExpiredResumeToken):
        raise
    except InvalidResumeToken as err:
        if "token was issued for" in str(err):
            raise
        token_exc = err
    except Exception as err:
        token_exc = err

    run = eff_engine.storage.get_run(run_id)
    if run is None or run.status is not RunStatus.PAUSED:
        raise ChowkiStateError(f"chowki run {run_id} is not paused")
    # Captured before the pause is cleared below: a gate re-applies the human patch
    # when the replay falls through it, an auto-pause has no gate in the body.
    pause_origin = run.pause.origin if run.pause is not None else "gate"

    if token_exc is not None:
        raise token_exc
    if claims is None:
        raise InvalidResumeToken("invalid resume token")

    snaps = eff_engine.storage.snapshots_for_resume(run_id)
    raw_state = eff_engine.pipeline_for(run_id).load(snaps)
    # `reviewed` stays the state the human was shown; `state` becomes the decided state.
    reviewed: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
    state: dict[str, Any] = reviewed
    state_hash_before = content_hash(reviewed)
    audit_log = AuditLog(eff_engine.storage, redactor=eff_engine.redactor)

    # The human's patch is redacted once, here, and only this form is ever used: it is
    # what the decided state is computed from and what the audit log records. Redacting
    # the two routes separately diverged under a sensitive key, where applying the raw
    # value yields `[REDACTED:key_name:...]` while the replay -- which applies the audit
    # log's already-redacted patch -- keeps the value's own `[REDACTED:<kind>:...]`
    # placeholder, so `state_hash_after` described a document no replay could rebuild.
    # `redact_patch`, not a plain `redact`, because an op's value must be judged by the
    # key it is destined for: a human's `hunter2` is a secret only under `/password`.
    effective_patch: Patch = redact_patch(eff_engine.redactor, patch) if patch else []

    if decision is Decision.REJECT:
        audit_record = build_audit_record(
            run_id=run_id,
            step_id=claims.step_id,
            action=decision.value,
            actor=actor,
            original_state_hash=state_hash_before,
            patched_state_hash=state_hash_before,
            json_patch=[],
            nonce=claims.nonce,
            note=note,
        )
        audit_log.append(audit_record)
        _confirm_gateway(eff_engine, run_id, decision, actor)
        run.status = RunStatus.REJECTED
        eff_engine.storage.put_run(run)
        eff_engine.drop_pipeline(run_id)
        raise HumanRejectedError(run_id, claims.step_id, note=note)

    if decision is Decision.EDIT and effective_patch:
        state = cast(dict[str, Any], apply_patch(state, effective_patch))
    elif decision is Decision.ESCALATE:
        current_pause = run.pause
        permitted = (
            current_pause.permitted_actions if current_pause is not None else ("APPROVE", "REJECT")
        )
        new_token = eff_engine.tokens.issue(
            run_id=run_id,
            step_id=claims.step_id,
            permitted_actions=permitted,
        )
        audit_record = build_audit_record(
            run_id=run_id,
            step_id=claims.step_id,
            action=decision.value,
            actor=actor,
            original_state_hash=state_hash_before,
            patched_state_hash=state_hash_before,
            json_patch=effective_patch,
            nonce=claims.nonce,
            note=note,
        )
        audit_log.append(audit_record)
        _confirm_gateway(eff_engine, run_id, decision, actor)
        if current_pause is not None:
            rev_val = actor.get("reviewers") if actor is not None else None
            revs = tuple(rev_val) if isinstance(rev_val, list) else current_pause.reviewers
            run.pause = msgspec_replace(current_pause, reviewers=cast(tuple[str, ...], revs))
            eff_engine.storage.put_run(run)
        raise WorkflowPaused(run_id, claims.step_id, token=new_token)

    # The snapshot pipeline redacts everything it stores, so the decided state has to be
    # redacted here too for `state_hash_after` to name the document that gets persisted:
    # a patch value that is not itself a secret still becomes a placeholder when it lands
    # under a sensitive key. Redaction is a fixpoint over its own placeholders, so the
    # pipeline's second pass over this tree changes nothing.
    state = eff_engine.redactor.redact(state)
    state_hash_after = content_hash(state)

    audit_record = build_audit_record(
        run_id=run_id,
        step_id=claims.step_id,
        action=decision.value,
        actor=actor,
        original_state_hash=state_hash_before,
        patched_state_hash=state_hash_after,
        json_patch=effective_patch,
        nonce=claims.nonce,
        note=note,
    )
    audit_log.append(audit_record)
    _confirm_gateway(eff_engine, run_id, decision, actor)

    run.pause = None
    run.status = RunStatus.RUNNING
    eff_engine.storage.put_run(run)

    if state_hash_after != state_hash_before:
        _persist_state_of_record(eff_engine, run, state)

    # A gate pause is seeded with the state the human reviewed, not the patched one:
    # `pause()` applies the patch when it falls through the gate, which is the point in
    # the body where the human's decision belongs, and seeding the patched state would
    # apply the edit twice for any key the replay does not rewrite. An auto-pause has
    # no gate anywhere in the body -- nothing would ever apply the patch -- so the
    # decided (patched, redacted) state seeds the replay directly.
    seed = reviewed if pause_origin == "gate" else state
    eff_engine.pending_resume_state[run_id] = (claims.step_id, seed)

    val = _invoke_workflow(workflow_fn, run_id)

    return ResumeResult(
        run_id=run_id,
        decision=decision,
        value=val,
        state_hash_before=state_hash_before,
        state_hash_after=state_hash_after,
    )

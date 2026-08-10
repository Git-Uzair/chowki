"""Warm resume engine with state patching (ADR-004).

Warning (R4):
    Every side effect in a Chowki workflow must live inside a `@chowki.step`.
    Because `resume()` re-executes the workflow function body from the top,
    any side effect outside a `@chowki.step` will be re-executed on warm resume.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from msgspec.structs import replace as msgspec_replace

from chowki.config import ChowkiEngine, get_engine
from chowki.core.context import StateDict
from chowki.errors import (
    ChowkiStateError,
    ExpiredResumeToken,
    HumanRejectedError,
    InvalidResumeToken,
    ReplayedNonceError,
    WorkflowPaused,
)
from chowki.hitl.tokens import ResumeClaims
from chowki.state.canonical import content_hash
from chowki.state.delta import Patch, apply_patch
from chowki.types import Decision, JSONObject, RunStatus


@dataclass(frozen=True, slots=True)
class ResumeResult:
    run_id: str
    decision: Decision
    value: object
    state_hash_before: str
    state_hash_after: str


def _extract_root_keys(patch: Patch | None) -> set[str]:
    if not patch:
        return set()
    keys: set[str] = set()
    for op in patch:
        path = op.get("path")
        if isinstance(path, str) and path.startswith("/"):
            parts = path.lstrip("/").split("/")
            if parts and parts[0]:
                keys.add(parts[0])
    return keys


def _invoke_workflow(workflow_fn: Callable[..., Any], run_id: str) -> Any:
    try:
        return workflow_fn(run_id=run_id)
    except TypeError as exc:
        if exc.__traceback__ is not None and exc.__traceback__.tb_next is not None:
            raise
        if "unexpected keyword argument" in str(exc) or "run_id" in str(exc):
            return workflow_fn()
        raise


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

    if token_exc is not None:
        raise token_exc
    if claims is None:
        raise InvalidResumeToken("invalid resume token")

    snaps = eff_engine.storage.snapshots_for_resume(run_id)
    raw_state = eff_engine.pipeline_for(run_id).load(snaps)
    state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
    state_hash_before = content_hash(state)

    if decision is Decision.REJECT:
        audit_record: dict[str, Any] = {
            "audit_id": f"aud_{uuid4().hex[:16]}",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "run_id": run_id,
            "step_id": claims.step_id,
            "actor": actor or {},
            "action": decision.value,
            "original_state_hash": state_hash_before,
            "patched_state_hash": state_hash_before,
            "json_patch": [],
            "verification_details": {
                "signature_type": "chowki_hmac_sha256",
                "nonce": claims.nonce,
                "signature_verified": True,
            },
        }
        if note:
            audit_record["note"] = note
        eff_engine.storage.append_audit(audit_record)
        run.status = RunStatus.REJECTED
        eff_engine.storage.put_run(run)
        eff_engine.drop_pipeline(run_id)
        raise HumanRejectedError(run_id, claims.step_id, note=note)

    if decision is Decision.EDIT and patch:
        state = cast(dict[str, Any], apply_patch(state, patch))
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
        audit_record = {
            "audit_id": f"aud_{uuid4().hex[:16]}",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "run_id": run_id,
            "step_id": claims.step_id,
            "actor": actor or {},
            "action": decision.value,
            "original_state_hash": state_hash_before,
            "patched_state_hash": state_hash_before,
            "json_patch": patch or [],
            "verification_details": {
                "signature_type": "chowki_hmac_sha256",
                "nonce": claims.nonce,
                "signature_verified": True,
            },
        }
        if note:
            audit_record["note"] = note
        eff_engine.storage.append_audit(audit_record)
        if current_pause is not None:
            rev_val = actor.get("reviewers") if actor is not None else None
            revs = tuple(rev_val) if isinstance(rev_val, list) else current_pause.reviewers
            run.pause = msgspec_replace(current_pause, reviewers=cast(tuple[str, ...], revs))
            eff_engine.storage.put_run(run)
        raise WorkflowPaused(run_id, claims.step_id, token=new_token)

    state_hash_after = content_hash(state)

    audit_record = {
        "audit_id": f"aud_{uuid4().hex[:16]}",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "step_id": claims.step_id,
        "actor": actor or {},
        "action": decision.value,
        "original_state_hash": state_hash_before,
        "patched_state_hash": state_hash_after,
        "json_patch": patch or [],
        "verification_details": {
            "signature_type": "chowki_hmac_sha256",
            "nonce": claims.nonce,
            "signature_verified": True,
        },
    }
    if note:
        audit_record["note"] = note
    eff_engine.storage.append_audit(audit_record)

    run.pause = None
    run.status = RunStatus.RUNNING
    eff_engine.storage.put_run(run)

    frozen_keys = _extract_root_keys(patch if decision is Decision.EDIT else None)
    resumed_state = StateDict(state, frozen_keys=frozen_keys)
    eff_engine.pending_resume_state[run_id] = (claims.step_id, resumed_state)

    val = _invoke_workflow(workflow_fn, run_id)

    return ResumeResult(
        run_id=run_id,
        decision=decision,
        value=val,
        state_hash_before=state_hash_before,
        state_hash_after=state_hash_after,
    )

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, ParamSpec, TypeVar, cast, overload
from uuid import uuid4

import structlog

from chowki.config import ChowkiEngine, get_engine
from chowki.core.context import RunContext, current_run, run_scope
from chowki.core.registry import register_workflow
from chowki.errors import (
    BudgetExceeded,
    ChowkiConfigError,
    ChowkiStateError,
    HumanRejectedError,
    InfiniteLoopDetected,
    WorkflowPaused,
    classify,
)
from chowki.guardrails.breaker import AnomalyBreaker, BreakerAction
from chowki.hitl.gateway import PauseNotice
from chowki.state.delta import Patch, apply_patch
from chowki.types import JSONObject, PauseRequest, RunRecord, RunStatus, Usage

P = ParamSpec("P")
R = TypeVar("R")


def _open_run(
    engine: ChowkiEngine,
    workflow_name: str,
    run_id: str | None,
    tenant_id: str | None,
) -> tuple[RunContext, RunRecord]:
    eff_tenant = tenant_id or engine.config.tenant_id
    rid = run_id or f"run_{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    existing = engine.storage.get_run(rid)
    resuming = existing is not None

    if existing is None:
        record = RunRecord(
            run_id=rid,
            workflow=workflow_name,
            tenant_id=eff_tenant,
            created_at_utc=now,
            updated_at_utc=now,
            status=RunStatus.RUNNING,
        )
    else:
        record = existing
        record.status = RunStatus.RUNNING
        record.updated_at_utc = now

    engine.storage.put_run(record)

    try:
        # Snapshot indices continue above everything already stored, while ordinals
        # restart at 0 so the replay reproduces the same step and gate ids. Sharing one
        # counter would make a replayed ordinal overwrite the snapshot it first wrote.
        stored_max = engine.storage.max_snapshot_index(rid)
        start_snapshot_index = (stored_max if stored_max is not None else -1) + 1

        snaps = engine.storage.snapshots_for_resume(rid)
        state: dict[str, Any] = {}
        resumed_step_ids: set[str] = set()

        # The audit log, not this process, is the record of which gates a human has
        # already decided and what they changed: a run resumed twice, or resumed after a
        # restart, has to fall through every earlier gate and re-apply every earlier edit.
        resumed_patches: dict[str, Patch] = {}
        audits = engine.storage.list_audit(run_id=rid)
        for a in audits:
            action = a.get("action")
            sid = a.get("step_id")
            if action not in ("APPROVE", "EDIT") or not isinstance(sid, str):
                continue
            resumed_step_ids.add(sid)
            ops = a.get("json_patch")
            if action == "EDIT" and isinstance(ops, list) and ops:
                resumed_patches.setdefault(sid, []).extend(cast(Patch, ops))

        if rid in engine.pending_resume_state:
            res_step_id, pending_state = engine.pending_resume_state.pop(rid)
            resumed_step_ids.add(res_step_id)
            state = pending_state
        elif resuming and snaps:
            loaded = engine.pipeline_for(rid).load(snaps)
            if isinstance(loaded, dict):
                state = loaded

        ctx = RunContext(
            run_id=rid,
            workflow=workflow_name,
            engine=engine,
            state=state,
            resuming=resuming,
            resumed_step_ids=resumed_step_ids,
            resumed_patches=resumed_patches,
            usage=existing.usage if existing is not None else Usage(),
            _snapshot_index=start_snapshot_index,
        )
        if existing is not None and existing.usage:
            ctx.budget.total = existing.usage
        ctx.loops.reset()
        return ctx, record
    except BaseException:
        record.status = RunStatus.FAILED
        record.updated_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        engine.storage.put_run(record)
        engine.drop_pipeline(rid)
        raise


def _close_run(
    ctx: RunContext,
    record: RunRecord,
    exc: BaseException | None,
) -> None:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record.updated_at_utc = now

    # A paused run already snapshotted its state inside pause(). Snapshotting again would
    # record whatever the workflow body did after the pause -- state that was never
    # committed at the pause boundary -- as the state a resume loads.
    paused = ctx.pause is not None and (exc is None or isinstance(exc, WorkflowPaused))

    snapshot_exc: BaseException | None = None
    if not paused:
        try:
            ctx.engine.pipeline_for(ctx.run_id).snapshot(
                ctx.state,
                run_id=ctx.run_id,
                workflow=ctx.workflow,
                step_index=ctx.next_snapshot_index(),
            )
        except BaseException as snap_err:
            snapshot_exc = snap_err

    # A run that asked to pause is not complete, even if the workflow body swallowed the
    # WorkflowPaused: only a resume may move it off PAUSED.
    swallowed_pause = exc is None and ctx.pause is not None
    try:
        if exc is None and snapshot_exc is None and not swallowed_pause:
            record.status = RunStatus.COMPLETED
            record.usage = ctx.usage
            ctx.engine.storage.put_run(record)
            ctx.engine.drop_pipeline(ctx.run_id)
        elif (isinstance(exc, WorkflowPaused) or swallowed_pause) and snapshot_exc is None:
            record.status = RunStatus.PAUSED
            record.pause = ctx.pause
            record.usage = ctx.usage
            ctx.engine.storage.put_run(record)
        elif isinstance(exc, HumanRejectedError) and snapshot_exc is None:
            record.status = RunStatus.REJECTED
            record.usage = ctx.usage
            ctx.engine.storage.put_run(record)
            ctx.engine.drop_pipeline(ctx.run_id)
        else:
            record.status = RunStatus.FAILED
            record.usage = ctx.usage
            ctx.engine.storage.put_run(record)
            ctx.engine.drop_pipeline(ctx.run_id)
    finally:
        if snapshot_exc is not None and exc is None:
            raise snapshot_exc


@overload
def workflow(func: Callable[P, R]) -> Callable[..., R]: ...


@overload
def workflow(
    *,
    name: str | None = None,
    engine: ChowkiEngine | None = None,
    tenant_id: str | None = None,
    register: bool = True,
) -> Callable[[Callable[P, R]], Callable[..., R]]: ...


def workflow(
    func: Callable[P, R] | None = None,
    *,
    name: str | None = None,
    engine: ChowkiEngine | None = None,
    tenant_id: str | None = None,
    register: bool = True,
) -> Any:
    """Decorator to define a Chowki workflow.

    Note: A workflow function may not declare parameters named 'run_id' or 'tenant_id'
    because those parameter names are injected by the decorator at invocation time.
    """
    dec_tenant_id = tenant_id

    def decorator(fn: Callable[P, R]) -> Callable[..., R]:
        params = inspect.signature(fn).parameters
        if "run_id" in params or "tenant_id" in params:
            msg = (
                f"workflow function {fn.__name__!r} cannot declare parameters named"
                " 'run_id' or 'tenant_id'"
            )
            raise ChowkiConfigError(msg)

        workflow_name = name or fn.__name__

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(
                *args: Any,
                run_id: str | None = None,
                tenant_id: str | None = None,
                **kwargs: Any,
            ) -> Any:
                eff_engine = engine or get_engine()
                eff_tenant = tenant_id or dec_tenant_id
                ctx, record = _open_run(eff_engine, workflow_name, run_id, eff_tenant)
                exc_occurred: BaseException | None = None
                with run_scope(ctx):
                    try:
                        return await fn(*args, **kwargs)
                    except (WorkflowPaused, HumanRejectedError) as exc:
                        exc_occurred = exc
                        raise
                    except BaseException as exc:
                        suspended = _maybe_auto_pause(ctx, exc)
                        if suspended is None:
                            exc_occurred = exc
                            raise
                        exc_occurred = suspended
                        raise suspended from exc
                    finally:
                        _close_run(ctx, record, exc_occurred)

            wrapper = cast(Callable[..., R], async_wrapper)
        else:

            @functools.wraps(fn)
            def sync_wrapper(
                *args: Any,
                run_id: str | None = None,
                tenant_id: str | None = None,
                **kwargs: Any,
            ) -> Any:
                eff_engine = engine or get_engine()
                eff_tenant = tenant_id or dec_tenant_id
                ctx, record = _open_run(eff_engine, workflow_name, run_id, eff_tenant)
                exc_occurred: BaseException | None = None
                with run_scope(ctx):
                    try:
                        return fn(*args, **kwargs)
                    except (WorkflowPaused, HumanRejectedError) as exc:
                        exc_occurred = exc
                        raise
                    except BaseException as exc:
                        suspended = _maybe_auto_pause(ctx, exc)
                        if suspended is None:
                            exc_occurred = exc
                            raise
                        exc_occurred = suspended
                        raise suspended from exc
                    finally:
                        _close_run(ctx, record, exc_occurred)

            wrapper = cast(Callable[..., R], sync_wrapper)

        if register:
            register_workflow(workflow_name, wrapper)

        return wrapper

    if callable(func):
        return decorator(func)
    return decorator


def pause(
    *,
    reason: str,
    payload: JSONObject | None = None,
    permitted_actions: Sequence[str] = ("APPROVE", "REJECT"),
    reviewers: Sequence[str] = (),
    channel: str = "console",
) -> Any:
    """Suspend a run at a step boundary and mint a scope-bound, single-use resume token."""
    ctx = current_run()
    ordinal = ctx.next_ordinal()
    step_id = f"pause#{ordinal}"

    if step_id in ctx.resumed_step_ids:
        # A human already decided this gate, so fall through it instead of suspending
        # again -- and re-apply their patch here, at the gate, because this is the point
        # in the body where the decision was made. The live state at this point is the
        # replay of the state the human reviewed, so applying the recorded patch
        # reproduces the state they decided on, deletions included, and it does so at
        # every later gate and every later resume: the patch is read from the audit log,
        # not carried in this process.
        ctx.resumed_step_ids.remove(step_id)
        ctx.pause = None
        gate_patch = ctx.resumed_patches.get(step_id)
        if gate_patch:
            patched = apply_patch(ctx.state, gate_patch)
            if not isinstance(patched, dict):
                raise ChowkiStateError(
                    f"human patch for {step_id} of run {ctx.run_id} replaced the state root "
                    f"with {type(patched).__name__}; chowki state must stay a JSON object"
                )
            ctx.state.clear()
            ctx.state.update(cast(JSONObject, patched))
        return None

    token = _suspend(
        ctx,
        step_id=step_id,
        reason=reason,
        payload=payload,
        permitted_actions=permitted_actions,
        reviewers=reviewers,
        channel=channel,
        origin="gate",
    )
    raise WorkflowPaused(ctx.run_id, step_id, token=token)


def _suspend(
    ctx: RunContext,
    *,
    step_id: str,
    reason: str,
    payload: JSONObject | None,
    permitted_actions: Sequence[str],
    reviewers: Sequence[str],
    channel: str,
    origin: str,
) -> str:
    """Durably suspend the run: freeze state, mint a token, persist, notify.

    Shared by the ``pause()`` gate and the guardrail auto-pause; the caller owns
    gate bookkeeping and raising WorkflowPaused with the returned token.
    """
    redacted_payload = (
        cast(JSONObject, ctx.engine.redactor.redact(payload)) if payload is not None else {}
    )
    # The pause boundary is the state a resume must see. ctx.state is the live dict the
    # workflow keeps mutating, so it has to be frozen here rather than by _close_run.
    ctx.engine.pipeline_for(ctx.run_id).snapshot(
        ctx.state,
        run_id=ctx.run_id,
        workflow=ctx.workflow,
        step_index=ctx.next_snapshot_index(),
    )
    pause_req = PauseRequest(
        step_id=step_id,
        reason=reason,
        permitted_actions=tuple(permitted_actions),
        payload=redacted_payload,
        reviewers=tuple(reviewers),
        channel=channel,
        created_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        origin=origin,
    )
    token = ctx.engine.tokens.issue(
        run_id=ctx.run_id,
        step_id=step_id,
        permitted_actions=permitted_actions,
    )
    ctx.pause = pause_req
    # Persisted here, not only in _close_run: the suspension must be durable even if the
    # workflow body catches WorkflowPaused and never lets the wrapper see it.
    record = ctx.engine.storage.get_run(ctx.run_id)
    if record is not None:
        record.status = RunStatus.PAUSED
        record.pause = pause_req
        record.usage = ctx.usage
        record.updated_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        ctx.engine.storage.put_run(record)

    notice = PauseNotice(
        run_id=ctx.run_id,
        workflow=ctx.workflow,
        step_id=step_id,
        reason=reason,
        payload=redacted_payload,
        permitted_actions=tuple(permitted_actions),
        reviewers=tuple(reviewers),
        token=token,
        created_at_utc=pause_req.created_at_utc,
        channel=channel,
    )
    gateway = ctx.engine.gateway
    if gateway is not None:
        try:
            handle = gateway.notify(notice)
            ctx.engine.storage.put_gateway_handle(ctx.run_id, handle)
        except Exception:
            logger = structlog.get_logger()
            logger.exception("chowki_gateway_notify_failed", run_id=ctx.run_id, channel=channel)

    return token


def _maybe_auto_pause(ctx: RunContext, exc: BaseException) -> WorkflowPaused | None:
    """Convert a breaker PAUSE decision into a real suspension (ADR-005).

    Fires for exactly two shapes: an exception a step's breaker already stamped
    with ``chowki_action = PAUSE`` (retry-exhausted tool/rate-limit errors,
    reask-exhausted validation), and a bare guardrail breach raised outside any
    step (loop detection inside ``_begin``, a hard budget breach in the body) --
    where the breaker is consulted here. Everything else stays a FAILED run.
    Returns the WorkflowPaused to raise, or None to let the failure stand. A
    failure inside suspension itself falls back to the plain failure path: a
    guardrail must never turn a recordable error into an unrecorded crash.
    """
    if not isinstance(exc, Exception) or ctx.pause is not None:
        return None

    action = getattr(exc, "chowki_action", None)
    if action is None and isinstance(exc, (BudgetExceeded, InfiniteLoopDetected)):
        action = AnomalyBreaker(ctx.engine.config.guardrails).decide(exc, attempt=0)
    if action is not BreakerAction.PAUSE:
        return None

    error_class = classify(exc).value
    step_id = getattr(exc, "chowki_step_id", None) or f"breach#{error_class}"
    try:
        token = _suspend(
            ctx,
            step_id=step_id,
            reason=f"auto-pause: {error_class}: {exc}",
            payload={"error_class": error_class, "message": str(exc), "step_id": step_id},
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
            reviewers=(),
            channel="console",
            origin="auto",
        )
    except Exception:
        logger = structlog.get_logger()
        logger.exception("chowki_auto_pause_failed", run_id=ctx.run_id, step_id=step_id)
        return None

    logger = structlog.get_logger()
    logger.warning(
        "chowki_run_auto_paused",
        run_id=ctx.run_id,
        step_id=step_id,
        error_class=error_class,
    )
    return WorkflowPaused(ctx.run_id, step_id, token=token)


def reissue_token(
    run_id: str,
    *,
    engine: ChowkiEngine | None = None,
    notify: bool = True,
) -> str:
    """Mint a fresh resume token for a PAUSED run from its stored pause request.

    The escape hatch for a token that was lost, expired, or burnt by a resume
    attempt that failed after nonce consumption: without it a paused run whose
    token is gone can never move again. Each issued token carries its own
    single-use nonce, so reissuing does not revoke earlier unconsumed tokens;
    scope stays what the pause granted (same step, same permitted actions).
    With ``notify`` (the default) the configured gateway is notified again so
    reviewers receive the new token where they received the original.
    """
    eff_engine = engine or get_engine()
    run = eff_engine.storage.get_run(run_id)
    if run is None or run.status is not RunStatus.PAUSED or run.pause is None:
        raise ChowkiStateError(f"chowki run {run_id} is not paused")

    pause_req = run.pause
    token = eff_engine.tokens.issue(
        run_id=run_id,
        step_id=pause_req.step_id,
        permitted_actions=pause_req.permitted_actions,
    )
    logger = structlog.get_logger()
    logger.warning("chowki_resume_token_reissued", run_id=run_id, step_id=pause_req.step_id)

    gateway = eff_engine.gateway
    if notify and gateway is not None:
        notice = PauseNotice(
            run_id=run_id,
            workflow=run.workflow,
            step_id=pause_req.step_id,
            reason=pause_req.reason,
            payload=pause_req.payload,
            permitted_actions=pause_req.permitted_actions,
            reviewers=pause_req.reviewers,
            token=token,
            created_at_utc=pause_req.created_at_utc,
            channel=pause_req.channel,
        )
        try:
            handle = gateway.notify(notice)
            eff_engine.storage.put_gateway_handle(run_id, handle)
        except Exception:
            logger.exception(
                "chowki_gateway_notify_failed", run_id=run_id, channel=pause_req.channel
            )

    return token


def resumable_runs(engine: ChowkiEngine) -> list[RunRecord]:
    """List all incomplete (non-terminal) runs in storage."""
    resumable = {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED}
    return [r for r in engine.storage.list_runs() if r.status in resumable]


def recover_runs(engine: ChowkiEngine) -> list[RunRecord]:
    """Detect incomplete runs, reset RUNNING runs to PENDING on process start, and return them."""
    logger = structlog.get_logger()
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    incomplete = resumable_runs(engine)
    for run in incomplete:
        if run.status is RunStatus.RUNNING:
            run.status = RunStatus.PENDING
            run.updated_at_utc = now
            engine.storage.put_run(run)
            logger.info("chowki_run_recovered", run_id=run.run_id)
    return incomplete

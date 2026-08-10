from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, ParamSpec, TypeVar, cast, overload
from uuid import uuid4

import structlog

from chowki.config import ChowkiEngine, get_engine
from chowki.core.context import RunContext, StateDict, current_run, run_scope
from chowki.errors import ChowkiConfigError, HumanRejectedError, WorkflowPaused
from chowki.types import JSONObject, PauseRequest, RunRecord, RunStatus

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
        all_snapshots = engine.storage.list_snapshots(rid)
        all_steps = engine.storage.list_steps(rid)
        max_snap_idx = max((e.step_index for e in all_snapshots), default=-1)
        max_step_ord = max((s.ordinal for s in all_steps), default=-1)
        start_ordinal = (
            0 if rid in engine.pending_resume_state else max(max_snap_idx, max_step_ord) + 1
        )

        snaps = engine.storage.snapshots_for_resume(rid)
        state: dict[str, Any] = {}
        resumed_step_ids: set[str] = set()

        if rid in engine.pending_resume_state:
            res_step_id, pending_state = engine.pending_resume_state.pop(rid)
            resumed_step_ids.add(res_step_id)
            state = StateDict(pending_state)
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
            _ordinal=start_ordinal,
        )
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
                step_index=ctx.next_ordinal(),
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
            ctx.engine.storage.put_run(record)
        elif isinstance(exc, HumanRejectedError) and snapshot_exc is None:
            record.status = RunStatus.REJECTED
            ctx.engine.storage.put_run(record)
            ctx.engine.drop_pipeline(ctx.run_id)
        else:
            record.status = RunStatus.FAILED
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
) -> Callable[[Callable[P, R]], Callable[..., R]]: ...


def workflow(
    func: Callable[P, R] | None = None,
    *,
    name: str | None = None,
    engine: ChowkiEngine | None = None,
    tenant_id: str | None = None,
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
                    except BaseException as exc:
                        exc_occurred = exc
                        raise
                    finally:
                        _close_run(ctx, record, exc_occurred)

            return cast(Callable[..., R], async_wrapper)
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
                    except BaseException as exc:
                        exc_occurred = exc
                        raise
                    finally:
                        _close_run(ctx, record, exc_occurred)

            return cast(Callable[..., R], sync_wrapper)

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
        ctx.resumed_step_ids.remove(step_id)
        ctx.pause = None
        if isinstance(ctx.state, StateDict):
            ctx.state.unfreeze()
        return None

    redacted_payload = (
        cast(JSONObject, ctx.engine.redactor.redact(payload)) if payload is not None else {}
    )
    # The pause boundary is the state a resume must see. ctx.state is the live dict the
    # workflow keeps mutating, so it has to be frozen here rather than by _close_run.
    ctx.engine.pipeline_for(ctx.run_id).snapshot(
        ctx.state,
        run_id=ctx.run_id,
        workflow=ctx.workflow,
        step_index=ordinal,
    )
    pause_req = PauseRequest(
        step_id=step_id,
        reason=reason,
        permitted_actions=tuple(permitted_actions),
        payload=redacted_payload,
        reviewers=tuple(reviewers),
        channel=channel,
        created_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
        record.updated_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        ctx.engine.storage.put_run(record)
    # wired in Task 21
    raise WorkflowPaused(ctx.run_id, step_id, token=token)


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

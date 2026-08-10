from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, ParamSpec, TypeVar, cast, overload
from uuid import uuid4

import structlog

from chowki.config import ChowkiEngine, get_engine
from chowki.core.context import RunContext, run_scope
from chowki.errors import ChowkiConfigError, HumanRejectedError, WorkflowPaused
from chowki.types import RunRecord, RunStatus

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
        engine.storage.put_run(record)
    else:
        record = existing
        record.status = RunStatus.RUNNING
        record.updated_at_utc = now
        engine.storage.put_run(record)

    snaps = engine.storage.snapshots_for_resume(rid)
    state: dict[str, Any] = {}
    if resuming and snaps:
        loaded = engine.pipeline_for(rid).load(snaps)
        if isinstance(loaded, dict):
            state = loaded

    ctx = RunContext(
        run_id=rid,
        workflow=workflow_name,
        engine=engine,
        state=state,
        resuming=resuming,
    )
    return ctx, record


def _close_run(
    ctx: RunContext,
    record: RunRecord,
    exc: BaseException | None,
) -> None:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record.updated_at_utc = now

    ctx.engine.pipeline_for(ctx.run_id).snapshot(
        ctx.state,
        run_id=ctx.run_id,
        workflow=ctx.workflow,
        step_index=ctx.next_ordinal(),
    )

    if exc is None:
        record.status = RunStatus.COMPLETED
        record.usage = ctx.usage
        ctx.engine.storage.put_run(record)
        ctx.engine.drop_pipeline(ctx.run_id)
    elif isinstance(exc, WorkflowPaused):
        record.status = RunStatus.PAUSED
        record.pause = ctx.pause
        ctx.engine.storage.put_run(record)
    elif isinstance(exc, HumanRejectedError):
        record.status = RunStatus.REJECTED
        ctx.engine.storage.put_run(record)
        ctx.engine.drop_pipeline(ctx.run_id)
    else:
        record.status = RunStatus.FAILED
        ctx.engine.storage.put_run(record)
        ctx.engine.drop_pipeline(ctx.run_id)


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

from __future__ import annotations

import functools
import hashlib
import hmac
import inspect
import traceback
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Final, ParamSpec, TypeVar, cast, overload

import structlog

from chowki.core.context import RunContext, current_run, in_run
from chowki.errors import ChowkiStorageError, classify
from chowki.state.canonical import content_hash
from chowki.state.codec import decode_state, encode_state
from chowki.types import JSONValue, StepError, StepRecord, StepStatus

_UNSERIALIZABLE: Final = "__chowki_unserializable__"
_MISSING: Final = object()

P = ParamSpec("P")
R = TypeVar("R")


def _signature(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    def _sanitize(val: object) -> Any:
        if val is None or isinstance(val, (bool, int, float, str)):
            return val
        if isinstance(val, dict):
            d = cast(dict[object, object], val)
            return {str(k): _sanitize(v) for k, v in d.items()}
        if isinstance(val, (list, tuple, set, frozenset)):
            seq = cast(Sequence[object], val)
            return [_sanitize(x) for x in seq]
        return f"<{type(val).__name__}>"

    return {
        "name": name,
        "args": _sanitize(args),
        "kwargs": _sanitize(kwargs),
    }


def _begin(
    ctx: RunContext,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    idempotent: bool,
) -> tuple[StepRecord, Any]:
    step_id = ctx.next_step_id(name)
    ordinal = ctx.next_ordinal()
    sig = _signature(name, args, kwargs)
    args_hash = content_hash(sig)

    existing = ctx.engine.storage.get_step(ctx.run_id, step_id)
    if existing is not None and existing.status is StepStatus.COMPLETED:
        if existing.args_hash == args_hash:
            if existing.result is not None:
                decoded = decode_state(existing.result)
                if isinstance(decoded, dict) and _UNSERIALIZABLE in decoded:
                    pass
                else:
                    return existing, decoded
            else:
                return existing, None
        else:
            logger = structlog.get_logger()
            logger.warning(
                "chowki_step_args_changed",
                step_id=step_id,
                run_id=ctx.run_id,
                expected=existing.args_hash,
                actual=args_hash,
            )

    engine_secret = ctx.engine.resume_secret
    msg = f"{ctx.run_id}|{step_id}|{args_hash}".encode()
    idempotency_key = hmac.new(engine_secret, msg, hashlib.sha256).hexdigest()

    if existing is None and idempotent:
        claimed = ctx.engine.storage.claim_idempotency_key(idempotency_key, args_hash=args_hash)
        if not claimed:
            raise ChowkiStorageError(
                f"idempotency key {idempotency_key} already claimed for step {step_id}"
            )

    # Guardrail pre-checks (Tasks 16-17) go here behind ctx.engine.guardrails
    # wired in Task 16/17

    started_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record = StepRecord(
        run_id=ctx.run_id,
        step_id=step_id,
        name=name,
        ordinal=ordinal,
        idempotency_key=idempotency_key,
        args_hash=args_hash,
        started_at_utc=started_at_utc,
        status=StepStatus.RUNNING,
        attempts=existing.attempts if existing is not None else 0,
    )
    ctx.step_records[step_id] = record
    ctx.engine.storage.put_step(record)
    return record, _MISSING


def _succeed(
    ctx: RunContext,
    record: StepRecord,
    result: Any,
    snapshot: bool,
) -> None:
    try:
        redacted = cast(JSONValue, ctx.engine.redactor.redact(result))
        encoded_result = encode_state(redacted)
    except Exception:
        encoded_result = encode_state({_UNSERIALIZABLE: type(result).__name__})

    record.status = StepStatus.COMPLETED
    record.ended_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record.attempts += 1
    record.result = encoded_result

    ctx.step_records[record.step_id] = record
    ctx.engine.storage.put_step(record)

    if snapshot:
        ctx.engine.pipeline_for(ctx.run_id).snapshot(
            ctx.state,
            run_id=ctx.run_id,
            workflow=ctx.workflow,
            step_index=record.ordinal,
        )


def _fail(
    ctx: RunContext,
    record: StepRecord,
    exc: BaseException,
) -> None:
    record.status = StepStatus.FAILED
    record.ended_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record.attempts += 1
    record.error = StepError(
        error_class=classify(exc).value,
        message=str(exc),
        traceback=traceback.format_exc(),
    )
    ctx.step_records[record.step_id] = record
    ctx.engine.storage.put_step(record)


@overload
def step(func: Callable[P, R]) -> Callable[P, R]: ...


@overload
def step(
    *,
    name: str | None = None,
    idempotent: bool = True,
    snapshot: bool = True,
    retries: int | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def step(
    func: Callable[P, R] | None = None,
    *,
    name: str | None = None,
    idempotent: bool = True,
    snapshot: bool = True,
    retries: int | None = None,
) -> Any:
    """Interceptor for step memoisation, idempotency, and state snapshotting."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        step_name = name or fn.__name__

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                if not in_run():
                    res = await fn(*args, **kwargs)
                    return cast(R, res)
                ctx = current_run()
                rec, memoised = _begin(ctx, step_name, args, kwargs, idempotent)
                if memoised is not _MISSING:
                    return cast(R, memoised)
                try:
                    res = await fn(*args, **kwargs)
                except Exception as exc:
                    _fail(ctx, rec, exc)
                    raise
                else:
                    _succeed(ctx, rec, res, snapshot)
                    return cast(R, res)

            return cast(Callable[P, R], async_wrapper)
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                if not in_run():
                    return fn(*args, **kwargs)
                ctx = current_run()
                rec, memoised = _begin(ctx, step_name, args, kwargs, idempotent)
                if memoised is not _MISSING:
                    return cast(R, memoised)
                try:
                    res = fn(*args, **kwargs)
                except Exception as exc:
                    _fail(ctx, rec, exc)
                    raise
                else:
                    _succeed(ctx, rec, res, snapshot)
                    return res

            return sync_wrapper

    if callable(func):
        return decorator(func)
    return decorator

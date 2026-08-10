from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import inspect
import math
import time
import traceback
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Final, ParamSpec, TypeVar, cast, overload

import structlog

from chowki.core.context import RunContext, current_run, in_run
from chowki.core.runner import workflow
from chowki.errors import ChowkiStorageError, HumanRejectedError, WorkflowPaused, classify
from chowki.guardrails.breaker import AnomalyBreaker, BreakerAction
from chowki.state.canonical import content_hash
from chowki.state.codec import decode_state, encode_state
from chowki.types import JSONValue, StepError, StepRecord, StepStatus

_UNSERIALIZABLE: Final = "__chowki_unserializable__"
_MISSING: Final = object()
_CYCLE: Final = "<cycle>"

P = ParamSpec("P")
R = TypeVar("R")


def _signature(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Reduce a call to a JSON-shaped description whose content hash is a resume key.

    The hash has to be identical in the process that resumes a run, so nothing that
    varies per process may reach it: set iteration order is salted by ``PYTHONHASHSEED``
    and object addresses are not stable, hence the sort and the ``<type>`` fallback.
    ``seen`` holds the ids of the containers on the path to the current value, so a
    self-referential argument degrades to a marker instead of exhausting the stack,
    while a value merely repeated between siblings is still described in full.

    The result must also be something :func:`canonicalize` accepts, because an argument
    the hasher rejects would abort the step before it ever starts. The two values it
    refuses -- non-finite floats and keys that collide once NFC-normalized -- are folded
    into a marker and a normalized key here.
    """

    def _sanitize(val: object, seen: frozenset[int]) -> Any:
        if isinstance(val, float) and not math.isfinite(val):
            # nan/inf have no JSON form; describing the type keeps the step alive.
            return f"<{type(val).__name__}>"
        if val is None or isinstance(val, (bool, int, float, str)):
            return val
        if id(val) in seen:
            return _CYCLE
        inner = seen | {id(val)}
        if isinstance(val, dict):
            d = cast(dict[object, object], val)
            return {unicodedata.normalize("NFC", str(k)): _sanitize(v, inner) for k, v in d.items()}
        if isinstance(val, (set, frozenset)):
            members = cast(Iterable[object], val)
            # Sorting on the repr of the already-sanitised member is a total order over
            # the JSON shapes this returns; equal reprs mean equal members, so the ties
            # it leaves cannot change the hash.
            return sorted((_sanitize(x, inner) for x in members), key=repr)
        if isinstance(val, (list, tuple)):
            seq = cast(Sequence[object], val)
            return [_sanitize(x, inner) for x in seq]
        return f"<{type(val).__name__}>"

    empty: frozenset[int] = frozenset()
    return {
        "name": name,
        "args": _sanitize(args, empty),
        "kwargs": _sanitize(kwargs, empty),
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
    # A record on its own only proves that some attempt *started*. Only COMPLETED with
    # the same arguments proves one finished, and that is the sole evidence that this
    # step's side effect is already accounted for.
    finished = False
    if existing is not None and existing.status is StepStatus.COMPLETED:
        if existing.args_hash == args_hash:
            finished = True
            if existing.result_replayable:
                if existing.result is None:
                    return existing, None
                return existing, decode_state(existing.result)
            # The step finished but its result cannot be replayed, so the body has to
            # run again. Only the record says so -- a step is free to return a dict that
            # looks exactly like the stored marker. The claim is deliberately left alone:
            # this step is already accounted for, and re-claiming a key we own would
            # abort the run.
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

    if idempotent and not finished:
        # Reaching here means no completed attempt is on record, so the side effect is
        # about to happen for what must be the first time. The key is deterministic
        # (`03-durable-execution.md:73`), so a refusal means someone already owns this
        # effect -- a concurrent worker, or an earlier attempt of this run that died
        # mid-step. Neither can be replayed safely; Task 18's breaker owns the policy
        # for what to do about it.
        claimed = ctx.engine.storage.claim_idempotency_key(idempotency_key, args_hash=args_hash)
        if not claimed:
            raise ChowkiStorageError(
                f"step {step_id} of run {ctx.run_id} has an unfinished idempotent attempt: "
                f"idempotency key {idempotency_key} is already claimed"
            )

    ctx.loops.record(name, args_hash)

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
    replayable = True
    try:
        redacted = cast(JSONValue, ctx.engine.redactor.redact(result))
        encoded_result = encode_state(redacted)
    except TypeError:
        # TypeError is the codec saying "no msgpack encoding exists for this type", which
        # is a legitimate result the run should survive. Every other encoder error is a
        # defect in the value (an out-of-range int, a non-finite float); swallowing it
        # here would report the step COMPLETED while discarding its real result.
        # The payload names the type for diagnostics only; `result_replayable` is what
        # `_begin` reads, so a step returning that same shape stays memoisable.
        replayable = False
        encoded_result = encode_state({_UNSERIALIZABLE: type(result).__name__})

    record.status = StepStatus.COMPLETED
    record.ended_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record.result = encoded_result
    record.result_replayable = replayable

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
    record.error = StepError(
        error_class=classify(exc).value,
        message=str(exc),
        traceback=traceback.format_exc(),
    )
    ctx.step_records[record.step_id] = record
    ctx.engine.storage.put_step(record)


def _get_breaker(ctx: RunContext, retries: int | None) -> AnomalyBreaker:
    if retries is not None:
        cfg = replace(ctx.engine.config.guardrails, max_auto_retries=retries)
    else:
        cfg = ctx.engine.config.guardrails
    return AnomalyBreaker(cfg)


def _handle_step_exception(
    ctx: RunContext,
    record: StepRecord,
    exc: Exception,
    breaker: AnomalyBreaker,
    attempt: int,
) -> BreakerAction:
    action = breaker.decide(exc, attempt=attempt)
    if action is not BreakerAction.RETRY:
        exc.chowki_action = action  # type: ignore[attr-defined]
        _fail(ctx, record, exc)
    return action


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
    """Interceptor for step memoisation, idempotency, snapshotting, and breaker retries.

    REASK and SUMMARIZE decisions are attached to the raised exception as
    `exc.chowki_action = action` and re-raised for higher-level wrappers or applications.
    """

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

                breaker = _get_breaker(ctx, retries)
                initial_attempts = rec.attempts
                attempt = 0
                while True:
                    rec.attempts = initial_attempts + attempt + 1
                    try:
                        res = await fn(*args, **kwargs)
                        _succeed(ctx, rec, res, snapshot)
                        return cast(R, res)
                    except (WorkflowPaused, HumanRejectedError):
                        raise
                    except Exception as exc:
                        action = _handle_step_exception(ctx, rec, exc, breaker, attempt)
                        if action is BreakerAction.RETRY:
                            await asyncio.sleep(breaker.backoff_seconds(attempt))
                            attempt += 1
                            continue
                        raise

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

                breaker = _get_breaker(ctx, retries)
                initial_attempts = rec.attempts
                attempt = 0
                while True:
                    rec.attempts = initial_attempts + attempt + 1
                    try:
                        res = fn(*args, **kwargs)
                        _succeed(ctx, rec, res, snapshot)
                        return res
                    except (WorkflowPaused, HumanRejectedError):
                        raise
                    except Exception as exc:
                        action = _handle_step_exception(ctx, rec, exc, breaker, attempt)
                        if action is BreakerAction.RETRY:
                            time.sleep(breaker.backoff_seconds(attempt))
                            attempt += 1
                            continue
                        raise

            return sync_wrapper

    if callable(func):
        return decorator(func)
    return decorator


__all__ = ["step", "workflow"]

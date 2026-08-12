from __future__ import annotations

import asyncio
import contextlib
import dataclasses
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

import msgspec
import structlog

from chowki.config import ChowkiEngine, get_engine
from chowki.core.context import RunContext, current_run, in_run
from chowki.core.runner import pause, workflow
from chowki.errors import (
    ChowkiStateError,
    ChowkiStorageError,
    HumanRejectedError,
    WorkflowPaused,
    classify,
)
from chowki.guardrails.breaker import AnomalyBreaker, BreakerAction
from chowki.state.canonical import content_hash
from chowki.state.codec import decode_state, encode_state
from chowki.telemetry.tracing import span_for_step
from chowki.types import JSONValue, StepError, StepRecord, StepStatus

_UNSERIALIZABLE: Final = "__chowki_unserializable__"
_MISSING: Final = object()
_CYCLE: Final = "<cycle>"
_OPAQUE: Final = object()

P = ParamSpec("P")
R = TypeVar("R")


def _expand(val: object) -> Any:
    """Return a JSON-shaped expansion of a complex value, or ``_OPAQUE``.

    Structs and dataclasses are unpacked field by field *before* ``to_builtins`` sees
    them, and only one level deep: ``to_builtins`` would convert a ``set`` field to a
    list in this process' set iteration order, which ``PYTHONHASHSEED`` salts, so the
    resume key would move on every restart. Left as a set, ``_sanitize`` recurses into
    the fields and sorts it into a total order instead.

    ``to_builtins`` then covers what has no sets to lose -- attrs classes, enums, bytes
    (base64), datetimes, UUIDs, Decimals -- in one C-accelerated call, and comes before
    ``__dict__`` because an enum member's ``__dict__`` holds enum machinery (including
    its own class) rather than its value. ``model_dump`` covers Pydantic without
    importing it and ``__dict__`` covers ordinary classes; both are read defensively,
    because a lazy proxy is free to raise anything at all from ``__getattr__`` and an
    argument must never kill the step it describes.

    Only a value none of those describes falls back to a type marker, and ``_begin``
    warns when that happens: the silent version of that fallback is what let two
    different instances of one class share an args_hash and replay each other's
    memoised result.
    """
    if isinstance(val, msgspec.Struct):
        with contextlib.suppress(Exception):
            return msgspec.structs.asdict(val)
    if dataclasses.is_dataclass(val) and not isinstance(val, type):
        with contextlib.suppress(Exception):
            return {f.name: getattr(val, f.name) for f in dataclasses.fields(val)}
    with contextlib.suppress(TypeError, ValueError, RecursionError):
        return msgspec.to_builtins(val, str_keys=True)
    try:
        # Any error from the probe itself means "no structure available here", never
        # "abort the step": a lazy proxy raises ValueError for an unresolvable
        # reference just as readily as AttributeError for a missing one.
        dump = getattr(val, "model_dump", None)
    except Exception:
        dump = None
    if callable(dump):
        with contextlib.suppress(Exception):
            return dump()
    try:
        attrs = getattr(val, "__dict__", None)
    except Exception:  # same reason as the model_dump probe above
        attrs = None
    if isinstance(attrs, dict) and attrs:
        return dict(cast("dict[str, Any]", attrs))
    return _OPAQUE


def _signature(
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    opaque: list[str],
) -> dict[str, Any]:
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

    Complex values are expanded structurally by :func:`_expand` and hashed by value, so
    two instances of one class no longer share a hash. Only a value with no exposable
    structure at all collapses to a ``<TypeName>`` marker; its type name is appended to
    ``opaque`` so the caller can report which arguments lost their identity.
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
        expanded = _expand(val)
        type_name = type(val).__name__
        if expanded is _OPAQUE:
            opaque.append(type_name)
            return f"<{type_name}>"
        # Keyed by the type name so a Struct never hashes identically to an equal plain
        # dict, and re-sanitized because `model_dump`/`__dict__` can return anything.
        return {f"<{type_name}>": _sanitize(expanded, inner)}

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
    if ctx.pause is not None:
        # pause() froze the state a resume must load. A step allowed to run now would
        # claim keys, fire side effects and snapshot at a later snapshot index, and
        # `snapshots_for_resume` replays forward, so that snapshot would win over the
        # pause boundary. The run is suspended: the signal is re-raised instead. The
        # token is not repeated -- it is single-use and was delivered by the first raise.
        raise WorkflowPaused(ctx.run_id, ctx.pause.step_id)

    step_id = ctx.next_step_id(name)
    ordinal = ctx.next_ordinal()
    opaque_types: list[str] = []
    sig = _signature(name, args, kwargs, opaque_types)
    args_hash = content_hash(sig)
    if opaque_types:
        # A collapsed type means two different instances share this hash, so a memoised
        # result can replay for logically different arguments. Naming the type is what
        # lets the caller fix it by passing something with structure.
        structlog.get_logger().warning(
            "chowki_step_args_opaque",
            step_id=step_id,
            run_id=ctx.run_id,
            types=sorted(set(opaque_types)),
        )

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
        # mid-step. The one refusal that IS safe to override: the key embeds
        # run_id|step_id|args_hash under our secret, so a held claim next to a FAILED
        # record for the same identity can only be our own earlier attempt, and that
        # attempt's fate is known -- the body finished by raising. A RUNNING record (or
        # no record at all) means the effect's fate is unknown, and only an operator
        # who has checked the downstream system may release it.
        claimed = ctx.engine.storage.claim_idempotency_key(idempotency_key, args_hash=args_hash)
        if not claimed:
            failed_before = (
                existing is not None
                and existing.status is StepStatus.FAILED
                and existing.args_hash == args_hash
            )
            if not failed_before:
                raise ChowkiStorageError(
                    f"step {step_id} of run {ctx.run_id} has an unfinished idempotent attempt: "
                    f"idempotency key {idempotency_key} is already claimed. If the owning "
                    f"attempt is dead and the side effect did not happen, release it with "
                    f"chowki.release_step({ctx.run_id!r}, {step_id!r}); if it did happen, "
                    f"record it with chowki.complete_step(...)"
                )
            logger = structlog.get_logger()
            logger.info(
                "chowki_step_retry_after_failure",
                step_id=step_id,
                run_id=ctx.run_id,
                previous_attempts=existing.attempts if existing is not None else 0,
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


def _encode_step_result(redactor: Any, result: Any) -> tuple[bytes, bool]:
    """Redact and encode a step result, degrading to a diagnostic marker.

    TypeError is the codec saying "no msgpack encoding exists for this type", which
    is a legitimate result the run should survive. Every other encoder error is a
    defect in the value (an out-of-range int, a non-finite float); swallowing it
    here would report the step COMPLETED while discarding its real result.
    The payload names the type for diagnostics only; `result_replayable` is what
    `_begin` reads, so a step returning that same shape stays memoisable.
    """
    try:
        redacted = cast(JSONValue, redactor.redact(result))
        return encode_state(redacted), True
    except TypeError:
        return encode_state({_UNSERIALIZABLE: type(result).__name__}), False


def _succeed(
    ctx: RunContext,
    record: StepRecord,
    result: Any,
    snapshot: bool,
) -> None:
    encoded_result, replayable = _encode_step_result(ctx.engine.redactor, result)

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
            step_index=ctx.next_snapshot_index(),
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
        # The workflow wrapper's auto-pause binds the suspension to the step that
        # failed, so a resume retries exactly this step.
        exc.chowki_step_id = record.step_id  # type: ignore[attr-defined]
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

    Non-RETRY breaker decisions are attached to the raised exception as
    `exc.chowki_action = action` (plus `exc.chowki_step_id`) and re-raised. The
    enclosing @chowki.workflow converts a PAUSE decision into a durable auto-pause
    (run PAUSED, resume token minted, gateway notified); REASK and SUMMARIZE stay
    signals for higher-level wrappers or applications.
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
                tracing_enabled = ctx.engine.config.tracing_enabled
                while True:
                    rec.attempts = initial_attempts + attempt + 1
                    try:
                        if tracing_enabled:
                            with span_for_step(step_name):
                                res = await fn(*args, **kwargs)
                        else:
                            res = await fn(*args, **kwargs)
                        break
                    except (WorkflowPaused, HumanRejectedError):
                        raise
                    except Exception as exc:
                        action = _handle_step_exception(ctx, rec, exc, breaker, attempt)
                        if action is BreakerAction.RETRY:
                            await asyncio.sleep(breaker.backoff_seconds(attempt))
                            attempt += 1
                            continue
                        raise

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

                breaker = _get_breaker(ctx, retries)
                initial_attempts = rec.attempts
                attempt = 0
                tracing_enabled = ctx.engine.config.tracing_enabled
                while True:
                    rec.attempts = initial_attempts + attempt + 1
                    try:
                        if tracing_enabled:
                            with span_for_step(step_name):
                                res = fn(*args, **kwargs)
                        else:
                            res = fn(*args, **kwargs)
                        break
                    except (WorkflowPaused, HumanRejectedError):
                        raise
                    except Exception as exc:
                        action = _handle_step_exception(ctx, rec, exc, breaker, attempt)
                        if action is BreakerAction.RETRY:
                            time.sleep(breaker.backoff_seconds(attempt))
                            attempt += 1
                            continue
                        raise

                _succeed(ctx, rec, res, snapshot)
                return res

            return sync_wrapper

    if callable(func):
        return decorator(func)
    return decorator


def release_step(run_id: str, step_id: str, *, engine: ChowkiEngine | None = None) -> bool:
    """Release the idempotency claim of a step whose owning attempt is dead.

    Operator escape hatch, never called on the normal execution path. Use it only
    after confirming in the downstream system that the step's side effect did NOT
    happen; the next execution of the run will run the step body again. If the
    effect DID happen, use :func:`complete_step` instead. Returns whether a claim
    was actually released. The step record is left as the dead attempt wrote it;
    the re-execution overwrites it.
    """
    eff_engine = engine or get_engine()
    record = eff_engine.storage.get_step(run_id, step_id)
    if record is None:
        raise ChowkiStateError(f"no step {step_id!r} recorded for run {run_id!r}")
    released = eff_engine.storage.release_idempotency_key(record.idempotency_key)
    logger = structlog.get_logger()
    logger.warning(
        "chowki_step_claim_released",
        run_id=run_id,
        step_id=step_id,
        released=released,
    )
    return released


def complete_step(
    run_id: str,
    step_id: str,
    result: Any,
    *,
    engine: ChowkiEngine | None = None,
) -> None:
    """Record a dead step attempt as COMPLETED with an operator-supplied result.

    Operator escape hatch, the counterpart of :func:`release_step`: use it after
    confirming in the downstream system that the side effect DID happen. The next
    execution of the run memoises ``result`` instead of running the body, provided
    the step is called with the same arguments. The claim is left in place -- a
    completed step never re-claims.
    """
    eff_engine = engine or get_engine()
    record = eff_engine.storage.get_step(run_id, step_id)
    if record is None:
        raise ChowkiStateError(f"no step {step_id!r} recorded for run {run_id!r}")
    encoded_result, replayable = _encode_step_result(eff_engine.redactor, result)
    record.status = StepStatus.COMPLETED
    record.ended_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    record.result = encoded_result
    record.result_replayable = replayable
    eff_engine.storage.put_step(record)
    logger = structlog.get_logger()
    logger.warning(
        "chowki_step_completed_by_operator",
        run_id=run_id,
        step_id=step_id,
        result_replayable=replayable,
    )


__all__ = ["complete_step", "pause", "release_step", "step", "workflow"]

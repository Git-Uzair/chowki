from __future__ import annotations

import dataclasses
import enum
import os
import subprocess
import sys
from pathlib import Path

import msgspec
import pytest
from structlog.testing import capture_logs

from chowki.config import ChowkiEngine
from chowki.core.context import RunContext, run_scope
from chowki.core.decorators import complete_step, release_step, step
from chowki.errors import ChowkiStorageError, ToolExecutionError
from chowki.types import StepStatus

#: Wire constant written into a step record whose result cannot be encoded.
UNSERIALIZABLE_MARKER = "__chowki_unserializable__"

#: Runs one step with a ``set`` argument in a fresh interpreter and prints its args_hash.
#: Set iteration order is salted by PYTHONHASHSEED, so this is the only way to prove the
#: resume key survives a process restart.
_ARGS_HASH_PROBE = """
from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import RunContext, run_scope
from chowki.core.decorators import step
from chowki.storage.memory import MemoryStorage


@step
def fanout(tags):
    return len(tags)


engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))
ctx = RunContext(run_id="p", workflow="w", engine=engine)
with run_scope(ctx):
    fanout({"alpha", "beta", "gamma", "delta", "epsilon", "zeta"})
record = engine.storage.get_step("p", "fanout#0")
print(record.args_hash)
"""


#: Same proof one level down: the sets are *fields of* a Struct and of a dataclass, which
#: is where an expansion that converts them to lists too early bakes the process-local
#: iteration order into the resume key.
_COMPLEX_HASH_PROBE = """
import dataclasses

import msgspec

from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import RunContext, run_scope
from chowki.core.decorators import step
from chowki.storage.memory import MemoryStorage

TAGS = frozenset({"alpha", "beta", "gamma", "delta", "epsilon", "zeta"})


class Invoice(msgspec.Struct):
    tags: frozenset[str]


@dataclasses.dataclass
class Order:
    tags: frozenset[str]


@step
def handle(payload):
    return 1


engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))
ctx = RunContext(run_id="p", workflow="w", engine=engine)
with run_scope(ctx):
    handle(Invoice(tags=TAGS))
    handle(Order(tags=TAGS))
print(engine.storage.get_step("p", "handle#0").args_hash)
print(engine.storage.get_step("p", "handle#1").args_hash)
"""


#: Runs one idempotent step against a SQLite file in a fresh interpreter. Phase ``crash``
#: kills the process with ``os._exit`` in the middle of the side effect, leaving a RUNNING
#: record behind; phase ``resume`` is the recovery process that must refuse to repeat it.
#: Nothing is shared between the two but the database file, which is the whole point: the
#: resume secret behind the idempotency key has to come back off disk.
_CRASH_RESUME_PROBE = r"""
import os
import sys

from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import RunContext, run_scope
from chowki.core.decorators import step
from chowki.errors import ChowkiStorageError
from chowki.storage.sqlite import SQLiteStorage

db_path, effects_path, phase = sys.argv[1], sys.argv[2], sys.argv[3]


@step
def send_invoice(customer):
    with open(effects_path, "a", encoding="utf-8") as fh:
        fh.write(customer + "\n")
        fh.flush()
    if phase == "crash":
        os._exit(17)
    return "sent"


engine = ChowkiEngine(ChowkiConfig(storage=SQLiteStorage(db_path)))
ctx = RunContext(run_id="run-1", workflow="billing", engine=engine)
try:
    with run_scope(ctx):
        send_invoice("acme")
except ChowkiStorageError:
    print("REFUSED")
finally:
    engine.close()
"""


class _Interrupted(BaseException):
    """Stands in for a process death mid-step: a BaseException the wrapper must not record."""


def _crash_resume_phase(db: Path, effects: Path, phase: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CRASH_RESUME_PROBE, str(db), str(effects), phase],
        capture_output=True,
        text=True,
        check=False,
    )


def _hashes_under_seed(probe: str, seed: str) -> list[str]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return proc.stdout.split()


@pytest.fixture
def ctx(engine: ChowkiEngine) -> RunContext:
    return RunContext(run_id="r1", workflow="demo", engine=engine)


def test_step_runs_normally_outside_a_workflow() -> None:
    """A chowki-decorated function must stay a plain callable when unmanaged."""

    @step
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_step_records_a_completed_record(ctx: RunContext) -> None:
    @step
    def add(a: int, b: int) -> int:
        return a + b

    with run_scope(ctx):
        assert add(2, 3) == 5

    records = ctx.engine.storage.list_steps("r1")
    assert len(records) == 1
    assert records[0].step_id == "add#0"
    assert records[0].status is StepStatus.COMPLETED
    assert records[0].attempts == 1
    assert records[0].ended_at_utc is not None


def test_completed_steps_are_skipped_on_re_execution(ctx: RunContext) -> None:
    """The zero-waste warm resume core: a COMPLETED step never runs twice."""
    calls: list[int] = []

    @step
    def expensive(n: int) -> int:
        calls.append(n)
        return n * 2

    with run_scope(ctx):
        assert expensive(21) == 42
    assert calls == [21]

    replay = RunContext(run_id="r1", workflow="demo", engine=ctx.engine, resuming=True)
    with run_scope(replay):
        assert expensive(21) == 42  # served from the step record
    assert calls == [21]  # the function body did not run again


def test_step_ordinals_disambiguate_repeated_calls(ctx: RunContext) -> None:
    @step
    def echo(v: str) -> str:
        return v

    with run_scope(ctx):
        echo("a")
        echo("b")

    assert [s.step_id for s in ctx.engine.storage.list_steps("r1")] == ["echo#0", "echo#1"]


def test_failure_is_recorded_and_re_raised(ctx: RunContext) -> None:
    from chowki.errors import ToolExecutionError

    @step(retries=0)
    def boom() -> None:
        raise ToolExecutionError("nope")

    with run_scope(ctx), pytest.raises(ToolExecutionError):
        boom()

    rec = ctx.engine.storage.get_step("r1", "boom#0")
    assert rec is not None
    assert rec.status is StepStatus.FAILED
    assert rec.error is not None
    assert rec.error.error_class == "ToolExecutionError"


def test_secrets_in_arguments_and_results_are_redacted(ctx: RunContext) -> None:
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0"

    @step
    def leak(token: str) -> dict[str, str]:
        return {"echoed": token}

    with run_scope(ctx):
        leak(secret)

    rec = ctx.engine.storage.get_step("r1", "leak#0")
    assert rec is not None and rec.result is not None
    assert secret.encode() not in rec.result


def test_idempotency_key_is_claimed_once_per_step(ctx: RunContext) -> None:
    @step(idempotent=True)
    def send() -> str:
        return "sent"

    with run_scope(ctx):
        send()

    key = ctx.engine.storage.get_step("r1", "send#0")
    assert key is not None
    assert (
        ctx.engine.storage.claim_idempotency_key(key.idempotency_key, args_hash=key.args_hash)
        is False
    )


def test_a_cleanly_failed_idempotent_step_retries_on_reinvocation(
    engine: ChowkiEngine,
) -> None:
    """A FAILED record with matching args proves the earlier attempt is accounted
    for, so re-running the workflow must execute the step again instead of
    refusing on the claim the dead attempt left behind."""
    calls: list[int] = []

    @step(retries=0)
    def send() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise ToolExecutionError("downstream 500")
        return "sent"

    ctx1 = RunContext(run_id="rr", workflow="demo", engine=engine)
    with run_scope(ctx1), pytest.raises(ToolExecutionError):
        send()

    ctx2 = RunContext(run_id="rr", workflow="demo", engine=engine, resuming=True)
    with run_scope(ctx2):
        assert send() == "sent"
    assert len(calls) == 2

    rec = engine.storage.get_step("rr", "send#0")
    assert rec is not None and rec.status is StepStatus.COMPLETED


def test_a_mid_step_death_still_refuses_until_released(engine: ChowkiEngine) -> None:
    """A RUNNING record means the side effect's fate is unknown: refuse to repeat
    it, but let an operator who has checked the downstream system release the
    claim so the run can move again."""
    calls: list[int] = []

    @step
    def charge() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise _Interrupted()
        return "charged"

    ctx1 = RunContext(run_id="rc", workflow="demo", engine=engine)
    with run_scope(ctx1), pytest.raises(_Interrupted):
        charge()

    ctx2 = RunContext(run_id="rc", workflow="demo", engine=engine, resuming=True)
    with run_scope(ctx2), pytest.raises(ChowkiStorageError, match="release_step"):
        charge()

    assert release_step("rc", "charge#0", engine=engine) is True

    ctx3 = RunContext(run_id="rc", workflow="demo", engine=engine, resuming=True)
    with run_scope(ctx3):
        assert charge() == "charged"
    assert len(calls) == 2


def test_complete_step_records_an_operator_result_that_memoises(
    engine: ChowkiEngine,
) -> None:
    """The other escape hatch: the operator confirmed the side effect DID happen,
    so the step is recorded COMPLETED with the operator-supplied result and the
    re-execution memoises it instead of running the body."""

    @step
    def charge() -> str:
        raise _Interrupted()

    ctx1 = RunContext(run_id="rf", workflow="demo", engine=engine)
    with run_scope(ctx1), pytest.raises(_Interrupted):
        charge()

    complete_step("rf", "charge#0", result="charged-by-hand", engine=engine)

    ctx2 = RunContext(run_id="rf", workflow="demo", engine=engine, resuming=True)
    with run_scope(ctx2):
        assert charge() == "charged-by-hand"

    rec = engine.storage.get_step("rf", "charge#0")
    assert rec is not None and rec.status is StepStatus.COMPLETED


def test_release_and_complete_step_reject_unknown_steps(engine: ChowkiEngine) -> None:
    from chowki.errors import ChowkiStateError

    with pytest.raises(ChowkiStateError):
        release_step("nope", "charge#0", engine=engine)
    with pytest.raises(ChowkiStateError):
        complete_step("nope", "charge#0", result=None, engine=engine)


def test_state_is_snapshotted_per_step(ctx: RunContext) -> None:
    @step
    def bump() -> None:
        from chowki.core.context import current_run

        val = current_run().state.get("n", 0)
        n = val if isinstance(val, int) else 0
        current_run().state["n"] = n + 1

    with run_scope(ctx):
        bump()
        bump()

    assert len(ctx.engine.storage.list_snapshots("r1")) == 2


@pytest.mark.asyncio
async def test_async_step_works_identically(engine: ChowkiEngine) -> None:
    calls: list[int] = []

    @step
    async def fetch(n: int) -> int:
        calls.append(n)
        return n + 1

    ctx = RunContext(run_id="r2", workflow="demo", engine=engine)
    with run_scope(ctx):
        assert await fetch(1) == 2

    replay = RunContext(run_id="r2", workflow="demo", engine=engine, resuming=True)
    with run_scope(replay):
        assert await fetch(1) == 2
    assert calls == [1]


def test_unserializable_results_do_not_break_the_run(ctx: RunContext) -> None:
    """A step returning a socket must still run; it just cannot be memoised."""

    class Opaque:
        pass

    @step
    def make() -> Opaque:
        return Opaque()

    with run_scope(ctx):
        assert isinstance(make(), Opaque)

    rec = ctx.engine.storage.get_step("r1", "make#0")
    assert rec is not None
    assert rec.status is StepStatus.COMPLETED
    # Re-executing must call the body again rather than return a bogus value.
    replay = RunContext(run_id="r1", workflow="demo", engine=ctx.engine, resuming=True)
    with run_scope(replay):
        assert isinstance(make(), Opaque)


def test_set_arguments_hash_identically_in_a_fresh_process() -> None:
    """The resume key must not move when PYTHONHASHSEED reorders a set argument."""
    assert _hashes_under_seed(_ARGS_HASH_PROBE, "1") == _hashes_under_seed(
        _ARGS_HASH_PROBE, "424242"
    )


def test_sets_inside_complex_arguments_hash_identically_in_a_fresh_process() -> None:
    """Same guarantee for a set that is a *field* of a Struct or of a dataclass.

    Expanding those with ``to_builtins`` turns the field into a list in this process'
    set iteration order, which is salted by PYTHONHASHSEED -- the resume key would then
    move under the run every time the process restarts.
    """
    assert _hashes_under_seed(_COMPLEX_HASH_PROBE, "1") == _hashes_under_seed(
        _COMPLEX_HASH_PROBE, "424242"
    )


def test_self_referential_arguments_do_not_recurse(ctx: RunContext) -> None:
    """A cyclic argument must degrade to a marker, not blow the Python stack."""
    cycle: list[object] = []
    cycle.append(cycle)

    @step
    def consume(value: list[object]) -> int:
        return len(value)

    with run_scope(ctx):
        assert consume(cycle) == 1

    rec = ctx.engine.storage.get_step("r1", "consume#0")
    assert rec is not None
    assert rec.status is StepStatus.COMPLETED


def test_idempotent_step_is_not_re_entered_after_an_unfinished_attempt(ctx: RunContext) -> None:
    """A RUNNING record proves the side effect started, not that it finished.

    Re-entering it would risk sending the same email twice, so the deterministic
    idempotency key must be claimed again and the duplicate claim must be refused.
    """
    calls: list[int] = []

    @step(idempotent=True)
    def send() -> str:
        calls.append(1)
        raise _Interrupted  # the process dies mid-side-effect

    with run_scope(ctx), pytest.raises(_Interrupted):
        send()

    interrupted = ctx.engine.storage.get_step("r1", "send#0")
    assert interrupted is not None
    assert interrupted.status is StepStatus.RUNNING

    replay = RunContext(run_id="r1", workflow="demo", engine=ctx.engine, resuming=True)
    with run_scope(replay), pytest.raises(ChowkiStorageError):
        send()
    assert calls == [1]


def test_a_crashed_idempotent_step_is_refused_by_a_second_process(tmp_path: Path) -> None:
    """The guard only earns its keep across a process boundary.

    A replay through the same live engine proves nothing: the secret behind the
    idempotency key must be reproducible from the database alone, because the crash the
    guard exists for is also what destroys any in-process state.
    """
    db = tmp_path / "chowki.db"
    effects = tmp_path / "effects.log"

    crashed = _crash_resume_phase(db, effects, "crash")
    assert crashed.returncode == 17, crashed.stderr
    assert effects.read_text(encoding="utf-8").splitlines() == ["acme"]

    resumed = _crash_resume_phase(db, effects, "resume")
    assert resumed.returncode == 0, resumed.stderr
    assert "REFUSED" in resumed.stdout
    assert effects.read_text(encoding="utf-8").splitlines() == ["acme"]


def test_non_finite_float_arguments_do_not_abort_the_step(ctx: RunContext) -> None:
    """``nan`` has no canonical JSON form, but it must not stop a run from starting."""

    @step
    def scale(factor: float) -> str:
        return repr(factor)

    with run_scope(ctx):
        assert scale(float("nan")) == "nan"
        assert scale(float("inf")) == "inf"

    records = ctx.engine.storage.list_steps("r1")
    assert [r.status for r in records] == [StepStatus.COMPLETED, StepStatus.COMPLETED]
    assert all(r.args_hash.startswith("sha256:") for r in records)


def test_unicode_equivalent_argument_keys_do_not_abort_the_step(ctx: RunContext) -> None:
    """Two keys that collide only after NFC normalization must not stop a run either."""

    @step
    def index(rows: dict[str, int]) -> int:
        return len(rows)

    with run_scope(ctx):
        assert index({"caf\u00e9": 1, "cafe\u0301": 2}) == 2

    rec = ctx.engine.storage.get_step("r1", "index#0")
    assert rec is not None
    assert rec.status is StepStatus.COMPLETED


def test_a_result_dict_shaped_like_the_marker_is_still_memoised(ctx: RunContext) -> None:
    """A user payload must not be able to impersonate the "not replayable" flag."""
    calls: list[int] = []

    @step
    def describe() -> dict[str, str]:
        calls.append(1)
        return {UNSERIALIZABLE_MARKER: "int"}

    with run_scope(ctx):
        assert describe() == {UNSERIALIZABLE_MARKER: "int"}

    replay = RunContext(run_id="r1", workflow="demo", engine=ctx.engine, resuming=True)
    with run_scope(replay):
        assert describe() == {UNSERIALIZABLE_MARKER: "int"}
    assert calls == [1]


def test_an_unencodable_result_is_not_silently_swallowed(ctx: RunContext) -> None:
    """Only ``TypeError`` means "no encoding exists"; an out-of-range int is a defect.

    Masking it behind the unserialisable marker would report the step COMPLETED while
    throwing the real result away, so the encoder error must surface.
    """
    calls: list[int] = []

    @step
    def too_big() -> int:
        calls.append(1)
        return 2**64  # one past msgpack's uint64 ceiling

    with run_scope(ctx), pytest.raises(OverflowError):
        too_big()

    assert len(calls) == 1
    rec = ctx.engine.storage.get_step("r1", "too_big#0")
    assert rec is not None
    assert rec.status is not StepStatus.COMPLETED
    assert rec.result is None


def test_a_marker_result_still_reports_the_type(ctx: RunContext) -> None:
    class Opaque:
        pass

    @step
    def make() -> Opaque:
        return Opaque()

    with run_scope(ctx):
        make()

    rec = ctx.engine.storage.get_step("r1", "make#0")
    assert rec is not None and rec.result is not None
    assert UNSERIALIZABLE_MARKER.encode() in rec.result


def test_decorator_preserves_metadata_and_signature() -> None:
    from typing import Any, cast

    @step
    def documented(a: int) -> int:
        """Docstring survives."""
        return a

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Docstring survives."
    assert cast(Any, documented).__wrapped__ is not None


def test_step_retries_a_rate_limit_then_succeeds(ctx: RunContext) -> None:
    from dataclasses import replace

    from chowki.errors import RateLimitError

    ctx.engine.config.guardrails = replace(
        ctx.engine.config.guardrails, retry_base_seconds=0.0001, retry_max_seconds=0.0001
    )

    attempts: list[int] = []

    @step
    def flaky() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise RateLimitError("429")
        return "ok"

    with run_scope(ctx):
        assert flaky() == "ok"

    rec = ctx.engine.storage.get_step("r1", "flaky#0")
    assert rec is not None
    assert rec.attempts == 3
    assert rec.status is StepStatus.COMPLETED


def test_step_does_not_retry_a_loop_detection(ctx: RunContext) -> None:
    from chowki.errors import InfiniteLoopDetected
    from chowki.guardrails.breaker import BreakerAction

    attempts: list[int] = []

    @step
    def looping() -> None:
        attempts.append(1)
        raise InfiniteLoopDetected("cycle")

    with run_scope(ctx), pytest.raises(InfiniteLoopDetected) as exc_info:
        looping()
    assert len(attempts) == 1
    assert getattr(exc_info.value, "chowki_action", None) is BreakerAction.PAUSE


class _Invoice(msgspec.Struct):
    invoice_id: str
    amount: int


@dataclasses.dataclass
class _Order:
    order_id: str


class _FakeModel:
    """Stands in for a Pydantic model without adding the dependency: chowki reaches for
    ``model_dump`` by duck typing, exactly as it would on a real BaseModel."""

    def __init__(self, ref: str) -> None:
        self.ref = ref

    def model_dump(self) -> dict[str, object]:
        return {"ref": self.ref}


class _Plain:
    def __init__(self, tag: str) -> None:
        self.tag = tag


class _Channel(enum.Enum):
    EMAIL = "email"
    SMS = "sms"


class _Hostile:
    """A lazy proxy of the kind ORMs and client SDKs hand out: every attribute chowki
    might probe for goes through ``__getattr__``, and this one refuses with a
    ``ValueError`` (a detached session, an unresolvable reference) instead of an
    ``AttributeError``. Hashing must survive it. ``__slots__`` is what routes the
    ``__dict__`` probe through ``__getattr__`` as well."""

    __slots__ = ()

    def __getattr__(self, name: str) -> object:
        raise ValueError(f"cannot resolve {name!r}")


def _hash_of(ctx: RunContext, step_id: str) -> str:
    record = ctx.engine.storage.get_step(ctx.run_id, step_id)
    assert record is not None
    return record.args_hash


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_Invoice(invoice_id="inv-1", amount=1), _Invoice(invoice_id="inv-999", amount=1)),
        (_Order(order_id="o-1"), _Order(order_id="o-2")),
        (_FakeModel("a"), _FakeModel("b")),
        (_Plain("a"), _Plain("b")),
    ],
    ids=["struct", "dataclass", "pydantic-like", "plain-object"],
)
def test_different_instances_of_one_class_hash_differently(
    ctx: RunContext, first: object, second: object
) -> None:
    """The Phase 1 collapse made these identical, so the second call replayed the first's
    memoised result -- a wrong answer, not a slow one (POSITIONING.md:223-232)."""
    seen: list[object] = []

    @step
    def handle(payload: object) -> str:
        seen.append(payload)
        return "done"

    with run_scope(ctx):
        handle(first)
        handle(second)

    assert len(seen) == 2
    assert _hash_of(ctx, "handle#0") != _hash_of(ctx, "handle#1")


def test_equal_complex_arguments_still_memoise(ctx: RunContext) -> None:
    """Structural hashing must not break the memoisation it exists to make trustworthy."""
    calls: list[int] = []

    @step
    def handle(payload: _Invoice) -> str:
        calls.append(1)
        return "done"

    with run_scope(ctx):
        handle(_Invoice(invoice_id="inv-1", amount=10))

    replay = RunContext(run_id=ctx.run_id, workflow=ctx.workflow, engine=ctx.engine)
    with run_scope(replay):
        assert handle(_Invoice(invoice_id="inv-1", amount=10)) == "done"

    assert calls == [1]


def test_a_struct_does_not_collide_with_an_equal_dict(ctx: RunContext) -> None:
    @step
    def handle(payload: object) -> str:
        return "done"

    with run_scope(ctx):
        handle(_Invoice(invoice_id="inv-1", amount=10))
        handle({"invoice_id": "inv-1", "amount": 10})

    assert _hash_of(ctx, "handle#0") != _hash_of(ctx, "handle#1")


def test_an_unexpandable_argument_warns_instead_of_collapsing_silently(
    ctx: RunContext,
) -> None:
    """``object()`` has no structure to hash. It still collapses -- loudly."""

    @step
    def handle(payload: object) -> str:
        return "done"

    with capture_logs() as logs, run_scope(ctx):
        handle(object())

    events = [entry for entry in logs if entry["event"] == "chowki_step_args_opaque"]
    assert events, "an opaque argument type must be reported"
    assert events[0]["types"] == ["object"]
    assert events[0]["step_id"] == "handle#0"


def test_an_argument_whose_getattr_raises_collapses_instead_of_killing_the_step(
    ctx: RunContext,
) -> None:
    """Probing an argument for structure must never abort the step it is describing."""

    @step
    def handle(payload: object) -> str:
        return "done"

    with capture_logs() as logs, run_scope(ctx):
        assert handle(_Hostile()) == "done"

    events = [entry for entry in logs if entry["event"] == "chowki_step_args_opaque"]
    assert events, "an argument that refuses every probe must be reported"
    assert events[0]["types"] == ["_Hostile"]


def test_enum_arguments_hash_by_value_without_a_warning(ctx: RunContext) -> None:
    """An enum member has structure -- its value -- so it must not be reported opaque.

    An enum instance also carries a non-empty ``__dict__`` full of enum machinery
    (``__objclass__`` is the class itself), so expanding objects generically before
    asking msgspec would turn every enum argument into a false collapse warning.
    """

    @step
    def handle(payload: object) -> str:
        return "done"

    with capture_logs() as logs, run_scope(ctx):
        handle(_Channel.EMAIL)
        handle(_Channel.SMS)

    assert _hash_of(ctx, "handle#0") != _hash_of(ctx, "handle#1")
    assert [entry for entry in logs if entry["event"] == "chowki_step_args_opaque"] == []

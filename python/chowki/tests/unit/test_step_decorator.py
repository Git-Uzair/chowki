from __future__ import annotations

import pytest

from chowki.config import ChowkiEngine
from chowki.core.context import RunContext, run_scope
from chowki.core.decorators import step
from chowki.types import StepStatus


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


def test_decorator_preserves_metadata_and_signature() -> None:
    from typing import Any, cast

    @step
    def documented(a: int) -> int:
        """Docstring survives."""
        return a

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Docstring survives."
    assert cast(Any, documented).__wrapped__ is not None

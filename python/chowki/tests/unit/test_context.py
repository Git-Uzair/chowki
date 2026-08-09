from __future__ import annotations

import asyncio

import pytest

from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import RunContext, current_run, in_run, run_scope
from chowki.storage.memory import MemoryStorage


@pytest.fixture
def engine() -> ChowkiEngine:
    return ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))


def test_outside_a_run_there_is_no_context(engine: ChowkiEngine) -> None:
    assert in_run() is False
    with pytest.raises(LookupError):
        current_run()


def test_run_scope_sets_and_restores(engine: ChowkiEngine) -> None:
    ctx = RunContext(run_id="r", workflow="w", engine=engine)
    with run_scope(ctx):
        assert in_run() is True
        assert current_run().run_id == "r"
    assert in_run() is False


def test_nested_scopes_restore_the_outer_context(engine: ChowkiEngine) -> None:
    outer = RunContext(run_id="outer", workflow="w", engine=engine)
    inner = RunContext(run_id="inner", workflow="w", engine=engine)
    with run_scope(outer):
        with run_scope(inner):
            assert current_run().run_id == "inner"
        assert current_run().run_id == "outer"


def test_step_ids_are_stable_and_monotonic(engine: ChowkiEngine) -> None:
    ctx = RunContext(run_id="r", workflow="w", engine=engine)
    assert ctx.next_step_id("fetch") == "fetch#0"
    assert ctx.next_step_id("fetch") == "fetch#1"
    assert ctx.next_step_id("write") == "write#0"
    assert ctx.next_step_id("fetch") == "fetch#2"


def test_concurrent_tasks_get_isolated_contexts(engine: ChowkiEngine) -> None:
    """contextvars, not globals: two asyncio tasks must not see each other's run."""

    async def body(run_id: str) -> str:
        with run_scope(RunContext(run_id=run_id, workflow="w", engine=engine)):
            await asyncio.sleep(0.01)
            return current_run().run_id

    async def main() -> list[str]:
        return list(await asyncio.gather(body("a"), body("b")))

    assert asyncio.run(main()) == ["a", "b"]

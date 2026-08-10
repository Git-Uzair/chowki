from __future__ import annotations

from typing import Any, cast

import pytest

from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import RunContext, run_scope
from chowki.core.decorators import step
from chowki.guardrails.config import GuardrailConfig
from chowki.storage.memory import MemoryStorage


@pytest.mark.benchmark
def test_step_overhead_on_tiny_state(benchmark: Any, assert_budget: Any) -> None:
    engine = ChowkiEngine(
        ChowkiConfig(
            storage=MemoryStorage(),
            guardrails=GuardrailConfig(max_steps_per_run=10**9),
        )
    )

    @step
    def noop(n: int) -> int:
        return n

    ctx = RunContext(run_id="bench", workflow="w", engine=engine)
    counter = {"i": 0}

    def _call() -> None:
        counter["i"] += 1
        noop(counter["i"])
        cast(Any, ctx)._counters.clear()
        ctx.step_records.clear()
        storage: Any = engine.storage
        if hasattr(storage, "_steps"):
            storage._steps.clear()
        ctx.loops.reset()

    with run_scope(ctx):
        benchmark(_call)
    assert_budget(benchmark, "step_decorator_overhead_us")
    engine.close()

from __future__ import annotations

from typing import Any

import pytest

from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import RunContext, run_scope
from chowki.core.decorators import step
from chowki.storage.memory import MemoryStorage


@pytest.mark.benchmark
def test_step_overhead_on_tiny_state(benchmark: Any, assert_budget: Any) -> None:
    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))

    @step
    def noop(n: int) -> int:
        return n

    ctx = RunContext(run_id="bench", workflow="w", engine=engine)

    def _call() -> None:
        with run_scope(ctx):
            noop(1)

    benchmark(_call)
    assert_budget(benchmark, "step_decorator_overhead_us")
    engine.close()

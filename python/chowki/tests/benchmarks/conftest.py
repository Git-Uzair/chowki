"""Benchmark-suite fixtures: budget assertion against pytest-benchmark stats."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from budgets import BUDGETS, limit_seconds


@pytest.fixture
def assert_budget() -> Callable[[Any, str], None]:
    """Fail the test if the benchmark's median exceeds the named budget.

    Median, not mean: a single GC pause or OS scheduling hiccup must not fail a
    build, but a real regression moves the median.
    """

    def _assert(benchmark: Any, name: str) -> None:
        if name not in BUDGETS:
            raise KeyError(f"unknown chowki budget: {name!r}")
        if getattr(benchmark, "disabled", False) or getattr(benchmark, "stats", None) is None:
            return
        stats = getattr(benchmark.stats, "stats", None)
        if stats is None or getattr(stats, "median", None) is None:
            return
        median = float(stats.median)
        allowed = limit_seconds(name)
        assert median <= allowed, (
            f"chowki budget breach: {name} median={median * 1000:.3f} ms "
            f"allowed={allowed * 1000:.3f} ms "
            f"(base {BUDGETS[name]} with tolerance applied)"
        )

    return _assert

from __future__ import annotations

from typing import Any

import pytest

from chowki.guardrails.budget import BudgetTracker
from chowki.guardrails.config import GuardrailConfig
from chowki.types import Usage


@pytest.mark.benchmark
def test_budget_tracking_per_step_within_budget(benchmark: Any, assert_budget: Any) -> None:
    t = BudgetTracker(GuardrailConfig(max_token_budget=10**12, max_cost_usd=10**9))
    usage = Usage(input_tokens=100, output_tokens=50, cost_usd=0.001)
    benchmark(t.add, usage)
    assert_budget(benchmark, "budget_track_step_us")

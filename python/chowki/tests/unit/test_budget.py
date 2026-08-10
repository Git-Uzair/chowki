from __future__ import annotations

import pytest

from chowki.errors import BudgetExceeded
from chowki.guardrails.budget import BudgetTracker, BudgetWarning
from chowki.guardrails.config import GuardrailConfig
from chowki.types import Usage


def test_no_limits_means_never_trips() -> None:
    t = BudgetTracker(GuardrailConfig())
    for _ in range(1000):
        t.add(Usage(input_tokens=10_000, cost_usd=100.0))
    assert t.total.billable_tokens == 10_000_000


def test_usage_accumulates_across_all_dimensions() -> None:
    t = BudgetTracker(GuardrailConfig(max_token_budget=10_000))
    t.add(Usage(input_tokens=10, output_tokens=5, reasoning_tokens=2, cached_input_tokens=99))
    assert t.total.billable_tokens == 17
    assert t.total.cached_input_tokens == 99


def test_soft_threshold_emits_a_warning_once() -> None:
    events: list[BudgetWarning] = []
    t = BudgetTracker(GuardrailConfig(max_token_budget=1000), on_warning=events.append)
    t.add(Usage(input_tokens=790))
    assert events == []
    t.add(Usage(input_tokens=20))  # crosses 80%
    assert len(events) == 1
    assert events[0].dimension == "tokens"
    assert 0.80 <= events[0].fraction < 1.0
    t.add(Usage(input_tokens=10))  # still under 100%
    assert len(events) == 1, "the soft warning must not repeat every step"


def test_hard_token_limit_raises() -> None:
    t = BudgetTracker(GuardrailConfig(max_token_budget=100))
    with pytest.raises(BudgetExceeded, match="token"):
        t.add(Usage(input_tokens=101))


def test_hard_cost_limit_raises() -> None:
    t = BudgetTracker(GuardrailConfig(max_cost_usd=1.0))
    with pytest.raises(BudgetExceeded, match="cost"):
        t.add(Usage(cost_usd=1.01))


def test_cached_input_tokens_do_not_count_towards_the_token_ceiling() -> None:
    t = BudgetTracker(GuardrailConfig(max_token_budget=100))
    t.add(Usage(cached_input_tokens=10_000))  # discounted, must not trip the ceiling


def test_check_before_call_predicts_the_breach() -> None:
    """A caller can ask 'will this request fit?' before spending the tokens."""
    t = BudgetTracker(GuardrailConfig(max_token_budget=100))
    t.add(Usage(input_tokens=90))
    assert t.would_exceed(Usage(input_tokens=5)) is False
    assert t.would_exceed(Usage(input_tokens=20)) is True


def test_remaining_reports_both_dimensions() -> None:
    t = BudgetTracker(GuardrailConfig(max_token_budget=100, max_cost_usd=2.0))
    t.add(Usage(input_tokens=40, cost_usd=0.5))
    assert t.remaining_tokens == 60
    assert t.remaining_cost_usd == pytest.approx(1.5)


def test_exception_carries_the_dimension_and_the_totals() -> None:
    t = BudgetTracker(GuardrailConfig(max_token_budget=10))
    with pytest.raises(BudgetExceeded) as excinfo:
        t.add(Usage(input_tokens=11))
    assert "11" in str(excinfo.value)
    assert "10" in str(excinfo.value)


def test_disabled_guardrails_bypass_the_tracker() -> None:
    t = BudgetTracker(GuardrailConfig(enabled=False, max_token_budget=1))
    t.add(Usage(input_tokens=10_000))

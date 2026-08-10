from __future__ import annotations

import pytest

from chowki.errors import (
    BudgetExceeded,
    ContextWindowExceeded,
    InfiniteLoopDetected,
    RateLimitError,
    ToolExecutionError,
    ValidationFailure,
)
from chowki.guardrails.breaker import AnomalyBreaker, BreakerAction
from chowki.guardrails.config import GuardrailConfig


@pytest.fixture
def breaker() -> AnomalyBreaker:
    return AnomalyBreaker(GuardrailConfig())


@pytest.mark.parametrize(
    ("exc", "attempt", "expected"),
    [
        (RateLimitError("429"), 0, BreakerAction.RETRY),
        (RateLimitError("429"), 1, BreakerAction.RETRY),
        (RateLimitError("429"), 2, BreakerAction.RETRY),
        (RateLimitError("429"), 3, BreakerAction.PAUSE),  # max_auto_retries=3
        (ToolExecutionError("boom"), 0, BreakerAction.RETRY),
        (ToolExecutionError("boom"), 2, BreakerAction.RETRY),
        (ToolExecutionError("boom"), 3, BreakerAction.PAUSE),
        (ValidationFailure("bad"), 0, BreakerAction.REASK),
        (ValidationFailure("bad"), 1, BreakerAction.REASK),
        (ValidationFailure("bad"), 2, BreakerAction.PAUSE),  # max_validation_reasks=2
        (ContextWindowExceeded("long"), 0, BreakerAction.SUMMARIZE),
        (ContextWindowExceeded("long"), 1, BreakerAction.ABORT),
        (InfiniteLoopDetected("cycle"), 0, BreakerAction.PAUSE),
        (InfiniteLoopDetected("cycle"), 5, BreakerAction.PAUSE),
        (BudgetExceeded("over"), 0, BreakerAction.PAUSE),
    ],
)
def test_action_matrix(
    breaker: AnomalyBreaker, exc: Exception, attempt: int, expected: BreakerAction
) -> None:
    assert breaker.decide(exc, attempt=attempt) is expected


def test_hard_budget_action_abort_overrides_pause() -> None:
    b = AnomalyBreaker(GuardrailConfig(hard_budget_action="ABORT"))
    assert b.decide(BudgetExceeded("over"), attempt=0) is BreakerAction.ABORT


def test_pause_degrades_to_abort_without_a_gateway() -> None:
    """Auto-pause is meaningless if nobody can approve; fail loudly instead of hanging."""
    b = AnomalyBreaker(GuardrailConfig(), hitl_available=False)
    assert b.decide(InfiniteLoopDetected("cycle"), attempt=0) is BreakerAction.ABORT


def test_summarize_degrades_to_abort_without_a_summarizer() -> None:
    b = AnomalyBreaker(GuardrailConfig(), summarizer_available=False)
    assert b.decide(ContextWindowExceeded("long"), attempt=0) is BreakerAction.ABORT


def test_backoff_grows_exponentially_and_is_capped() -> None:
    b = AnomalyBreaker(GuardrailConfig(retry_base_seconds=1.0, retry_max_seconds=30.0))
    for attempt, ceiling in [(0, 1.0), (1, 2.0), (2, 4.0), (3, 8.0), (10, 30.0)]:
        for _ in range(50):
            delay = b.backoff_seconds(attempt)
            assert 0.0 <= delay <= ceiling


def test_backoff_has_jitter() -> None:
    b = AnomalyBreaker(GuardrailConfig())
    delays = {b.backoff_seconds(3) for _ in range(50)}
    assert len(delays) > 10, "full jitter must not produce a constant delay"


def test_disabled_guardrails_always_abort() -> None:
    b = AnomalyBreaker(GuardrailConfig(enabled=False))
    assert b.decide(RateLimitError("429"), attempt=0) is BreakerAction.ABORT

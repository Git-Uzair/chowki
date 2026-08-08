# python/chowki/tests/unit/test_errors.py
from __future__ import annotations

import pytest

from chowki.errors import (
    AgentError,
    BudgetExceeded,
    ChowkiError,
    ContextWindowExceeded,
    ErrorClass,
    HumanRejectedError,
    InfiniteLoopDetected,
    RateLimitError,
    ToolExecutionError,
    ValidationFailure,
    WorkflowPaused,
    classify,
)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RateLimitError("429"), ErrorClass.RATE_LIMIT),
        (ContextWindowExceeded("too long"), ErrorClass.CONTEXT_WINDOW),
        (ToolExecutionError("boom"), ErrorClass.TOOL_EXECUTION),
        (ValidationFailure("bad json"), ErrorClass.VALIDATION),
        (InfiniteLoopDetected("cycle"), ErrorClass.INFINITE_LOOP),
        (BudgetExceeded("over"), ErrorClass.BUDGET),
    ],
)
def test_every_taxonomy_member_reports_its_class(exc: AgentError, expected: ErrorClass) -> None:
    assert exc.error_class is expected
    assert isinstance(exc, AgentError)
    assert isinstance(exc, ChowkiError)


def test_classify_maps_unknown_exceptions_to_tool_execution() -> None:
    assert classify(ValueError("weird")) is ErrorClass.TOOL_EXECUTION
    assert classify(RateLimitError("x")) is ErrorClass.RATE_LIMIT


def test_classify_recognises_provider_status_codes() -> None:
    class FakeProviderError(Exception):
        status_code = 429

    assert classify(FakeProviderError()) is ErrorClass.RATE_LIMIT


def test_control_flow_signals_are_not_agent_errors() -> None:
    """WorkflowPaused is control flow; the breaker must never retry it."""
    assert not isinstance(WorkflowPaused("r", "s"), AgentError)
    assert isinstance(WorkflowPaused("r", "s"), ChowkiError)
    assert isinstance(HumanRejectedError("r", "s"), ChowkiError)

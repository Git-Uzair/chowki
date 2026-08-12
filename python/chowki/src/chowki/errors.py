"""chowki error taxonomy (docs/research/04-guardrails.md:95-130)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorClass(StrEnum):
    RATE_LIMIT = "RateLimitError"
    CONTEXT_WINDOW = "ContextWindowExceeded"
    TOOL_EXECUTION = "ToolExecutionError"
    VALIDATION = "ValidationFailure"
    INFINITE_LOOP = "InfiniteLoopDetected"
    BUDGET = "BudgetExceeded"


class ChowkiError(Exception):
    """Base class for everything chowki raises."""


class ChowkiConfigError(ChowkiError): ...


class ChowkiStorageError(ChowkiError): ...


class ChowkiStateError(ChowkiError): ...


class ChowkiConcurrencyError(ChowkiError):
    """Two steps of one run tried to execute at the same time.

    Not a transient failure: step ordinals and the RFC 6902 snapshot chain are both
    strictly sequential per run, so the interleaving would have produced a state
    document no resume can rebuild. Parallel steps within a run are Phase 6
    (deterministic branch keys); until then, run steps sequentially or run independent
    runs concurrently.
    """


class SchemaVersionError(ChowkiStateError): ...


class SnapshotIntegrityError(ChowkiStateError): ...


class DecryptionError(ChowkiStateError): ...


class AgentError(ChowkiError):
    """Base for the six taxonomy classes the anomaly breaker acts on."""

    error_class: ErrorClass = ErrorClass.TOOL_EXECUTION

    def __init__(self, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable


class RateLimitError(AgentError):
    error_class = ErrorClass.RATE_LIMIT


class ContextWindowExceeded(AgentError):
    error_class = ErrorClass.CONTEXT_WINDOW


class ToolExecutionError(AgentError):
    error_class = ErrorClass.TOOL_EXECUTION


class ValidationFailure(AgentError):
    error_class = ErrorClass.VALIDATION


class HallucinationError(ValidationFailure):
    """Alias kept distinct for callers that separate schema drift from fabrication."""


class InfiniteLoopDetected(AgentError):
    error_class = ErrorClass.INFINITE_LOOP


class BudgetExceeded(AgentError):
    error_class = ErrorClass.BUDGET


class WorkflowPaused(ChowkiError):
    """Control-flow signal raised when a run suspends for human input."""

    def __init__(self, run_id: str, step_id: str, *, token: str | None = None) -> None:
        super().__init__(f"chowki run {run_id} paused at {step_id}")
        self.run_id = run_id
        self.step_id = step_id
        self.token = token


class HumanRejectedError(ChowkiError):
    def __init__(self, run_id: str, step_id: str, *, note: str | None = None) -> None:
        super().__init__(f"chowki run {run_id} rejected at {step_id}")
        self.run_id = run_id
        self.step_id = step_id
        self.note = note


class ResumeTokenError(ChowkiError): ...


class ExpiredResumeToken(ResumeTokenError): ...


class InvalidResumeToken(ResumeTokenError): ...


class ReplayedNonceError(ResumeTokenError): ...


_RATE_LIMIT_STATUS = frozenset({429, 529})


def classify(exc: BaseException) -> ErrorClass:
    """Map an arbitrary exception onto the chowki taxonomy.

    Order matters: explicit chowki classes first, then duck-typed provider
    attributes, then the conservative TOOL_EXECUTION default.
    """
    if isinstance(exc, AgentError):
        return exc.error_class
    status: Any = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and status in _RATE_LIMIT_STATUS:
        return ErrorClass.RATE_LIMIT
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return ErrorClass.RATE_LIMIT
    if "contextlength" in name or "contextwindow" in name:
        return ErrorClass.CONTEXT_WINDOW
    return ErrorClass.TOOL_EXECUTION

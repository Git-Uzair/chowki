"""chowki — in-process agent state preservation, guardrails, and warm resume."""

from __future__ import annotations

from chowki.config import ChowkiConfig, configure
from chowki.core.context import current_run
from chowki.core.decorators import step
from chowki.core.resume import resume
from chowki.core.runner import pause, recover_runs, resumable_runs, workflow
from chowki.errors import (
    BudgetExceeded,
    ChowkiError,
    HumanRejectedError,
    InfiniteLoopDetected,
    WorkflowPaused,
)
from chowki.guardrails.config import GuardrailConfig
from chowki.types import Decision, PauseRequest, RunStatus, StepStatus, Usage

try:
    from chowki._version import __version__
except ImportError:  # pragma: no cover - source tree without a build
    __version__ = "0.0.0+unknown"


def report_usage(usage: Usage) -> None:
    """Report model token or cost usage for budget tracking in current run context."""
    current_run().budget.add(usage)


__all__ = [
    "BudgetExceeded",
    "ChowkiConfig",
    "ChowkiError",
    "Decision",
    "GuardrailConfig",
    "HumanRejectedError",
    "InfiniteLoopDetected",
    "PauseRequest",
    "RunStatus",
    "StepStatus",
    "Usage",
    "WorkflowPaused",
    "__version__",
    "configure",
    "current_run",
    "pause",
    "recover_runs",
    "report_usage",
    "resumable_runs",
    "resume",
    "step",
    "workflow",
]

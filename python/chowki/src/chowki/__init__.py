"""chowki — in-process agent state preservation, guardrails, and warm resume."""

from __future__ import annotations

from chowki.config import ChowkiConfig, configure
from chowki.core.context import current_run
from chowki.core.decorators import complete_step, release_step, step
from chowki.core.resume import aresume, rerun, resume
from chowki.core.runner import pause, recover_runs, reissue_token, resumable_runs, workflow
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


def report_usage(usage: Usage | int) -> None:
    """Report model token or cost usage for budget tracking in current run context."""
    u = usage if isinstance(usage, Usage) else Usage(input_tokens=usage)
    ctx = current_run()
    ctx.usage = ctx.usage.merge(u)
    ctx.budget.add(u)


def record_text(text: str) -> None:
    """Feed prompt or response text to the current run's semantic loop tier.

    Near-duplicate consecutive texts (normalized Levenshtein similarity over the
    configured thresholds) warn and then raise InfiniteLoopDetected (ADR-005 tier 2).
    """
    current_run().loops.record_text(text)


def record_transition(src: str, dst: str) -> None:
    """Record an agent delegation edge for the current run's graph cycle tier.

    Edges seen at least twice form the graph; a cycle in it raises
    InfiniteLoopDetected (ADR-005 tier 3).
    """
    current_run().loops.record_transition(src, dst)


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
    "aresume",
    "complete_step",
    "configure",
    "current_run",
    "pause",
    "record_text",
    "record_transition",
    "recover_runs",
    "reissue_token",
    "release_step",
    "report_usage",
    "rerun",
    "resumable_runs",
    "resume",
    "step",
    "workflow",
]

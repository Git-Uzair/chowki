from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from chowki.guardrails.budget import BudgetTracker
from chowki.guardrails.loops import LoopDetector
from chowki.types import JSONObject, PauseRequest, StepRecord, Usage

if TYPE_CHECKING:
    from chowki.config import ChowkiEngine


class StateDict(dict[str, Any]):
    """Special dict that protects patched state keys from being overwritten
    by unpatched step re-executions during warm resume.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._frozen_keys: set[str] = set(self.keys())

    def unfreeze(self) -> None:
        self._frozen_keys.clear()

    def __setitem__(self, key: str, value: Any) -> None:
        if key in self._frozen_keys:
            return
        super().__setitem__(key, value)

    def update(self, *args: Any, **kwargs: Any) -> None:
        other = dict(*args, **kwargs)
        for k, v in other.items():
            self[k] = v

    def clear(self) -> None:
        self._frozen_keys.clear()
        super().clear()


def _default_state() -> JSONObject:
    return {}


def _default_step_records() -> dict[str, StepRecord]:
    return {}


def _default_counters() -> dict[str, int]:
    return {}


def _default_resumed_step_ids() -> set[str]:
    return set()


@dataclass(slots=True)
class RunContext:
    run_id: str
    workflow: str
    engine: ChowkiEngine
    state: JSONObject = field(default_factory=_default_state)
    resuming: bool = False
    resumed_step_ids: set[str] = field(default_factory=_default_resumed_step_ids)
    usage: Usage = field(default_factory=Usage)
    step_records: dict[str, StepRecord] = field(default_factory=_default_step_records)
    pause: PauseRequest | None = None
    loops: LoopDetector = field(init=False)
    budget: BudgetTracker = field(init=False)
    _counters: dict[str, int] = field(default_factory=_default_counters)
    _ordinal: int = 0

    def __post_init__(self) -> None:
        self.loops = LoopDetector(self.engine.config.guardrails)
        self.budget = BudgetTracker(self.engine.config.guardrails)

    def next_step_id(self, name: str) -> str:
        """In-memory ordinal counter for step identity.

        Invariant: step identity = (name, call ordinal within the run).
        This identity makes warm resume work: a workflow re-executed from the top
        produces the same step ids in the same order, so completed steps are found
        and skipped.
        """
        n = self._counters.get(name, 0)
        self._counters[name] = n + 1
        return f"{name}#{n}"

    def next_ordinal(self) -> int:
        """Monotonic per-run ordinal counter across all steps."""
        n = self._ordinal
        self._ordinal += 1
        return n


_CURRENT: ContextVar[RunContext | None] = ContextVar("chowki_run", default=None)


def current_run() -> RunContext:
    ctx = _CURRENT.get()
    if ctx is None:
        raise LookupError("no active chowki run; call this inside a @chowki.workflow")
    return ctx


def in_run() -> bool:
    return _CURRENT.get() is not None


@contextmanager
def run_scope(ctx: RunContext) -> Generator[RunContext, None, None]:
    token = _CURRENT.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT.reset(token)

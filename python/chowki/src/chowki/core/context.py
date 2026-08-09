from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chowki.types import JSONObject, PauseRequest, StepRecord, Usage

if TYPE_CHECKING:
    from chowki.config import ChowkiEngine


def _default_state() -> JSONObject:
    return {}


def _default_step_records() -> dict[str, StepRecord]:
    return {}


def _default_counters() -> dict[str, int]:
    return {}


@dataclass(slots=True)
class RunContext:
    run_id: str
    workflow: str
    engine: ChowkiEngine
    state: JSONObject = field(default_factory=_default_state)
    resuming: bool = False
    usage: Usage = field(default_factory=Usage)
    step_records: dict[str, StepRecord] = field(default_factory=_default_step_records)
    pause: PauseRequest | None = None
    _counters: dict[str, int] = field(default_factory=_default_counters)

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

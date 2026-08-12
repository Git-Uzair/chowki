from __future__ import annotations

import asyncio
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chowki.errors import ChowkiConcurrencyError
from chowki.guardrails.budget import BudgetTracker
from chowki.guardrails.loops import LoopDetector
from chowki.types import JSONObject, PauseRequest, StepRecord, Usage

if TYPE_CHECKING:
    from chowki.config import ChowkiEngine
    from chowki.state.delta import Patch


def _default_state() -> JSONObject:
    return {}


def _default_step_records() -> dict[str, StepRecord]:
    return {}


def _default_counters() -> dict[str, int]:
    return {}


def _default_resumed_step_ids() -> set[str]:
    return set()


def _default_resumed_patches() -> dict[str, Patch]:
    return {}


def _executor_id() -> tuple[int, int]:
    """Identify the thread and task a step body is running on.

    An asyncio task copies the context rather than the context *var*, so every task in a
    `gather` sees the same RunContext object -- the identity of the running task is the
    only thing that tells them apart. Outside a running loop there is no task and the
    thread carries the whole identity.
    """
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return (threading.get_ident(), id(task) if task is not None else 0)


@dataclass(slots=True)
class RunContext:
    run_id: str
    workflow: str
    engine: ChowkiEngine
    state: JSONObject = field(default_factory=_default_state)
    resuming: bool = False
    resumed_step_ids: set[str] = field(default_factory=_default_resumed_step_ids)
    #: step_id -> the RFC 6902 patch a human already applied at that gate, read from the
    #: audit log. `pause()` re-applies it when it falls through that gate, which is what
    #: keeps a human edit authoritative for the rest of the run instead of being undone
    #: by the pre-pause assignments the re-execution replays.
    resumed_patches: dict[str, Patch] = field(default_factory=_default_resumed_patches)
    usage: Usage = field(default_factory=Usage)
    step_records: dict[str, StepRecord] = field(default_factory=_default_step_records)
    pause: PauseRequest | None = None
    loops: LoopDetector = field(init=False)
    budget: BudgetTracker = field(init=False)
    _counters: dict[str, int] = field(default_factory=_default_counters)
    _ordinal: int = 0
    _snapshot_index: int = 0
    _step_owner: tuple[int, int] | None = None
    _active_step: str | None = None
    _step_depth: int = 0

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
        """Monotonic per-run ordinal counter across all steps.

        Ordinals are *identity*, not storage position: they restart at 0 on every
        execution of the body so that a re-executed workflow reproduces the same
        `pause#N` gate ids. Use :meth:`next_snapshot_index` for anything that writes.
        """
        n = self._ordinal
        self._ordinal += 1
        return n

    def next_snapshot_index(self) -> int:
        """Allocate the next unused snapshot index for this run.

        It continues above every index already in storage, so a resumed execution can
        never write over a snapshot an earlier execution wrote. Snapshot indices are
        deliberately *not* ordinals: a replay reproduces ordinals by design, and a
        replayed ordinal used as a storage key would overwrite the snapshot at that index
        -- including the base a human decision was written to -- leaving every later
        delta in the chain diffed against a document that no longer exists.
        """
        n = self._snapshot_index
        self._snapshot_index += 1
        return n

    @contextmanager
    def step_guard(self, step_name: str) -> Generator[None, None, None]:
        """Refuse a second concurrent step in this run instead of corrupting its state.

        Nesting is not concurrency: a step that calls another step runs on the same task
        and thread, so the owner matches and only the depth grows. A different task or
        thread entering while a step is live is the `asyncio.gather` case, and it is
        refused before any ordinal is allocated or any record written.
        """
        owner = _executor_id()
        if self._step_owner is not None and self._step_owner != owner:
            raise ChowkiConcurrencyError(
                f"step {step_name!r} of run {self.run_id} started while step "
                f"{self._active_step!r} is still running. Concurrent steps within one run "
                f"(asyncio.gather, asyncio.to_thread, thread pools) are not supported and "
                f"would corrupt the run's snapshot chain: run the steps sequentially, or "
                f"run independent workflow runs concurrently."
            )
        if self._step_owner is None:
            self._step_owner = owner
            self._active_step = step_name
        self._step_depth += 1
        try:
            yield
        finally:
            self._step_depth -= 1
            if self._step_depth == 0:
                self._step_owner = None
                self._active_step = None


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

"""Concurrency inside one run is refused, loudly, at the point it would corrupt state.

Step identity is a per-run ordinal and snapshots are a linear RFC 6902 chain, so two
steps running at once interleave ordinals and diff against each other's document. The
damage only surfaces on the next resume, which is the worst possible time to find it
(POSITIONING.md:194-206).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest

import chowki
from chowki.config import ChowkiEngine
from chowki.errors import ChowkiConcurrencyError


async def test_gather_over_two_steps_raises(engine: ChowkiEngine) -> None:
    @chowki.step
    async def slow(tag: str) -> str:
        await asyncio.sleep(0.01)
        return tag

    @chowki.workflow(engine=engine)
    async def fan_out() -> tuple[str, str]:
        return await asyncio.gather(slow("a"), slow("b"))

    with pytest.raises(ChowkiConcurrencyError, match="run-gather"):
        await fan_out(run_id="run-gather")


async def test_gather_raises_even_when_one_step_is_memoised(engine: ChowkiEngine) -> None:
    """The guard sits before the memo lookup: a cache hit still consumes an ordinal."""

    @chowki.step
    async def slow(tag: str) -> str:
        await asyncio.sleep(0.01)
        return tag

    @chowki.step
    async def quick(tag: str) -> str:
        return tag

    @chowki.workflow(engine=engine)
    async def warm() -> str:
        return await quick("a")

    await warm(run_id="run-memo")

    @chowki.workflow(engine=engine, name="warm")
    async def fan_out() -> tuple[str, str]:
        return await asyncio.gather(slow("s"), quick("a"))

    with pytest.raises(ChowkiConcurrencyError):
        await fan_out(run_id="run-memo")


async def test_nested_steps_are_not_concurrency(engine: ChowkiEngine) -> None:
    """`pay_invoice` -> `_transfer` is the README's own shape and must keep working."""

    @chowki.step
    async def inner(value: int) -> int:
        return value + 1

    @chowki.step
    async def outer(value: int) -> int:
        return await inner(value) * 2

    @chowki.workflow(engine=engine)
    async def pipeline() -> int:
        return await outer(1)

    assert await pipeline(run_id="run-nested") == 4


def test_nested_sync_steps_are_not_concurrency(engine: ChowkiEngine) -> None:
    @chowki.step
    def inner(value: int) -> int:
        return value + 1

    @chowki.step
    def outer(value: int) -> int:
        return inner(value) * 2

    @chowki.workflow(engine=engine)
    def pipeline() -> int:
        return outer(1)

    assert pipeline(run_id="run-nested-sync") == 4


async def test_sequential_awaits_are_allowed(engine: ChowkiEngine) -> None:
    @chowki.step
    async def one(tag: str) -> str:
        await asyncio.sleep(0)
        return tag

    @chowki.workflow(engine=engine)
    async def pipeline() -> list[str]:
        return [await one("a"), await one("b")]

    assert await pipeline(run_id="run-seq") == ["a", "b"]


async def test_gather_inside_a_step_is_refused_without_retries(engine: ChowkiEngine) -> None:
    """The refusal surfaces inside the outer step's body, where its breaker can see it.

    `ChowkiConcurrencyError` is not transient -- retrying it sleeps out the backoff four
    times and then converts a permanent programming error into a `ToolExecutionError`
    auto-pause, hiding the real cause from the caller.
    """

    attempts = 0

    @chowki.step
    async def leaf(tag: str) -> str:
        await asyncio.sleep(0.01)
        return tag

    @chowki.step
    async def fan_out() -> tuple[str, str]:
        nonlocal attempts
        attempts += 1
        return await asyncio.gather(leaf("a"), leaf("b"))

    @chowki.workflow(engine=engine)
    async def pipeline() -> tuple[str, str]:
        return await fan_out()

    with pytest.raises(ChowkiConcurrencyError):
        await pipeline(run_id="run-inner-gather")

    assert attempts == 1


async def test_to_thread_inside_a_step_is_refused_without_retries(engine: ChowkiEngine) -> None:
    """`asyncio.to_thread` copies the context, so the offloaded step sees the run."""

    attempts = 0

    @chowki.step
    def leaf() -> str:
        return "leaf"

    @chowki.step
    async def offload() -> str:
        nonlocal attempts
        attempts += 1
        return await asyncio.to_thread(leaf)

    @chowki.workflow(engine=engine)
    async def pipeline() -> str:
        return await offload()

    with pytest.raises(ChowkiConcurrencyError):
        await pipeline(run_id="run-inner-to-thread")

    assert attempts == 1


def test_context_copying_pool_inside_a_sync_step_is_refused_without_retries(
    engine: ChowkiEngine,
) -> None:
    """Same exemption in the sync wrapper, whose retry loop blocks the thread instead."""

    attempts = 0

    @chowki.step
    def leaf() -> str:
        return "leaf"

    @chowki.step
    def offload() -> str:
        nonlocal attempts
        attempts += 1
        ctx = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(ctx.run, leaf).result()

    @chowki.workflow(engine=engine)
    def pipeline() -> str:
        return offload()

    with pytest.raises(ChowkiConcurrencyError):
        pipeline(run_id="run-inner-pool")

    assert attempts == 1


def test_a_thread_that_does_not_inherit_the_context_runs_unmanaged(
    engine: ChowkiEngine,
) -> None:
    """The boundary limits.md documents: no run ContextVar, no guard and no durability.

    `ThreadPoolExecutor.submit` does not copy the caller's context, so the step body
    runs with `in_run()` false -- a plain function call, recorded nowhere.
    """

    @chowki.step
    def leaf() -> str:
        return "leaf"

    @chowki.workflow(engine=engine)
    def pipeline() -> str:
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(leaf).result()

    assert pipeline(run_id="run-bare-thread") == "leaf"
    assert engine.storage.get_step("run-bare-thread", "leaf#0") is None


def test_the_guard_releases_when_a_step_raises(engine: ChowkiEngine) -> None:
    """A failed step must not leave the run permanently 'busy'."""

    @chowki.step
    def boom() -> None:
        raise ValueError("no")

    @chowki.step
    def fine() -> str:
        return "ok"

    @chowki.workflow(engine=engine)
    def pipeline() -> str:
        with contextlib.suppress(ValueError):
            boom()
        return fine()

    assert pipeline(run_id="run-release") == "ok"

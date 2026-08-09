from __future__ import annotations

from typing import Any

import pytest

from chowki.state.delta import DeltaChain, Patch, make_patch


def _base() -> dict[str, Any]:
    return {"messages": [{"role": "user", "content": "m" * 400} for _ in range(2400)]}


@pytest.mark.benchmark
def test_diff_1mib_single_append_within_budget(benchmark: Any, assert_budget: Any) -> None:
    before = _base()
    after = {"messages": [*before["messages"], {"role": "assistant", "content": "ok"}]}
    patch = benchmark(make_patch, before, after)
    assert patch
    assert_budget(benchmark, "delta_diff_1mb_ms")


@pytest.mark.benchmark
def test_warm_resume_base_plus_10_deltas_within_budget(benchmark: Any, assert_budget: Any) -> None:
    base = _base()
    current = base
    patches: list[Patch] = []
    for i in range(10):
        nxt = {"messages": [*current["messages"], {"role": "assistant", "content": str(i)}]}
        patches.append(make_patch(current, nxt))
        current = nxt

    def _materialize() -> object:
        chain = DeltaChain(base=base, patches=list(patches))
        return chain.materialize()

    benchmark(_materialize)
    assert_budget(benchmark, "warm_resume_base_plus_10_deltas_ms")

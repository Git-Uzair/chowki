from __future__ import annotations

import gc
from typing import Any

import pytest

from chowki.state.blobs import BlobStore
from chowki.state.pipeline import SnapshotPipeline
from chowki.state.redact import Redactor


@pytest.mark.benchmark
def test_cold_load_of_a_base_plus_10_deltas(benchmark: Any, assert_budget: Any) -> None:
    """Reconstruct a run from stored envelopes, the way `chowki resume` does.

    Gated by `cold_load_base_plus_10_deltas_ms`, not the warm resume budget: replaying the
    chain in memory is 0.064 ms of this, and the rest is msgpack decode plus the blob and
    defensive-copy walks that only the cold path performs.
    """
    pipe = SnapshotPipeline(redactor=Redactor(hmac_key=b"b"), blobs=BlobStore(), tenant_id="t")
    state: dict[str, Any] = {
        "messages": [{"role": "user", "content": "m" * 400} for _ in range(2400)]
    }
    envs: list[Any] = []
    for i in range(11):
        state = {"messages": [*state["messages"], {"role": "a", "content": str(i)}]}
        envs.append(pipe.snapshot(state, run_id="r", workflow="w", step_index=i))

    gc.collect()
    benchmark(pipe.load, envs)
    assert_budget(benchmark, "cold_load_base_plus_10_deltas_ms")

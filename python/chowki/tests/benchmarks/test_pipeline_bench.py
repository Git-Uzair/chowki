# python/chowki/tests/benchmarks/test_pipeline_bench.py
from __future__ import annotations

from typing import Any

import pytest

from chowki.state.blobs import BlobStore
from chowki.state.crypto import KeyRing
from chowki.state.pipeline import SnapshotPipeline
from chowki.state.redact import Redactor


def _one_mib() -> dict[str, object]:
    """~1 MiB of realistic agent state with varying string content."""
    messages: list[dict[str, object]] = []
    roles = ("user", "assistant", "system", "tool")
    base_text = (
        "The analysis shows a stable trend in cycle {i}. "
        "Component {role} reports nominal operational parameters for task_{i}. "
        "Executing verification pass with status code 200 and total items processed {i_100}. "
        "No critical anomalies detected in section {sec} during execution run. "
    )
    for i in range(50):
        role = roles[i % 4]
        chunk = base_text.format(i=i, role=role, i_100=i * 100, sec=i % 10)
        content = (chunk * 100)[:20971]
        msg: dict[str, object] = {"role": role, "content": content}
        messages.append(msg)
    return {"messages": messages}


@pytest.mark.benchmark
def test_full_snapshot_1mib_within_total_budget(benchmark: Any, assert_budget: Any) -> None:
    """The headline number from docs/research/00-synthesis.md:164: < 2.0 ms."""
    state = _one_mib()
    redactor = Redactor(hmac_key=b"bench")
    keyring = KeyRing.from_key(b"k" * 32, key_id="k1")
    counter = {"i": 0}

    def _run() -> None:
        pipe = SnapshotPipeline(
            redactor=redactor,
            blobs=BlobStore(),
            keyring=keyring,
            tenant_id="t1",
        )
        counter["i"] += 1
        pipe.snapshot(state, run_id="r", workflow="w", step_index=0)

    benchmark(_run)
    assert counter["i"] > 0
    assert_budget(benchmark, "snapshot_total_1mb_ms")


@pytest.mark.benchmark
def test_dispatch_is_off_the_hot_path(benchmark: Any, assert_budget: Any) -> None:
    pipe = SnapshotPipeline(
        redactor=Redactor(hmac_key=b"bench"),
        blobs=BlobStore(),
        tenant_id="t1",
    )
    env = pipe.snapshot({"a": 1}, run_id="r", workflow="w", step_index=0)
    benchmark(pipe.dispatch, env)
    assert_budget(benchmark, "dispatch_ms")

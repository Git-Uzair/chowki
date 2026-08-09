# python/chowki/tests/benchmarks/test_pipeline_bench.py
from __future__ import annotations

from typing import Any

import pytest

from chowki.state.blobs import BlobStore
from chowki.state.crypto import KeyRing
from chowki.state.pipeline import SnapshotPipeline
from chowki.state.redact import Redactor


def _one_mib() -> dict[str, object]:
    """~1 MiB of agent state that stays *inline*.

    Every string is 400 characters, well under the 4096-byte blob threshold, so the
    timed call really does encode, hash and encrypt a megabyte instead of a handful
    of blob references.
    """
    return {"messages": [{"role": "assistant", "content": "m" * 400} for _ in range(2400)]}


@pytest.mark.benchmark
def test_full_snapshot_1mib_within_total_budget(benchmark: Any, assert_budget: Any) -> None:
    """End-to-end 1 MiB snapshot budget (< 2.5 ms base, < 3.75 ms allowed; Task 11 revision)."""
    state = _one_mib()
    redactor = Redactor(hmac_key=b"bench")
    keyring = KeyRing.from_key(b"k" * 32, key_id="k1")
    probe_blobs = BlobStore()
    probe = SnapshotPipeline(
        redactor=redactor, blobs=probe_blobs, keyring=keyring, tenant_id="t1"
    ).snapshot(state, run_id="probe", workflow="w", step_index=0)
    assert len(probe.payload) > 1_000_000, "the timed payload must really be ~1 MiB"
    assert len(probe_blobs) == 0, "no string may escape into the blob store"

    def _run() -> None:
        pipe = SnapshotPipeline(
            redactor=redactor,
            blobs=BlobStore(),
            keyring=keyring,
            tenant_id="t1",
        )
        pipe.snapshot(state, run_id="r", workflow="w", step_index=0)

    benchmark(_run)
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

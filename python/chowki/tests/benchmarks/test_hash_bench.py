from __future__ import annotations

import hashlib
import statistics
import time
from collections.abc import Callable
from typing import Any

import pytest
from budgets import HASH_OVERHEAD_MAX, REFERENCE_STATE_BYTES

from chowki.state.canonical import hash_bytes


def _paired_medians(
    baseline: Callable[[], object], measured: Callable[[], object], *, rounds: int = 200
) -> tuple[float, float]:
    """Median seconds for two callables, sampled alternately.

    Interleaved on purpose: a shared CI runner can lose a chunk of its throughput to a
    noisy neighbour partway through a test, and a sequential all-of-A-then-all-of-B
    comparison would charge that drift entirely to B.
    """
    baseline_ns: list[int] = []
    measured_ns: list[int] = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        baseline()
        baseline_ns.append(time.perf_counter_ns() - start)
        start = time.perf_counter_ns()
        measured()
        measured_ns.append(time.perf_counter_ns() - start)
    return statistics.median(baseline_ns) / 1e9, statistics.median(measured_ns) / 1e9


@pytest.mark.benchmark
def test_hash_1mib_within_budget(benchmark: Any, assert_budget: Any) -> None:
    """Gate chowki's share of the 1 MiB hash, not the CPU it happened to run on.

    Hashing a megabyte is OpenSSL's SHA-256 core; chowki contributes a hex encode and a
    seven-character prefix. The absolute median therefore reports hardware — 2.18 GB/s on
    the reference dev box, 1.40 GB/s on a shared CI runner — and a gate on it asserts which
    machine the build landed on rather than whether the library regressed. So the enforced
    gate is the ratio against the platform digest, with `canonical_hash_1mb_ceiling_ms` left
    as a loose absolute backstop against a catastrophic regression on any hardware.
    """
    payload = b"c" * REFERENCE_STATE_BYTES

    benchmark(hash_bytes, payload)
    assert_budget(benchmark, "canonical_hash_1mb_ceiling_ms")

    baseline, measured = _paired_medians(
        lambda: hashlib.sha256(payload).hexdigest(),
        lambda: hash_bytes(payload),
    )
    ratio = measured / baseline
    assert ratio <= HASH_OVERHEAD_MAX, (
        f"chowki budget breach: hash_bytes costs {ratio:.3f}x the platform SHA-256 digest "
        f"(allowed {HASH_OVERHEAD_MAX}x); baseline median={baseline * 1000:.3f} ms, "
        f"hash_bytes median={measured * 1000:.3f} ms. Something now walks the payload "
        f"more than once."
    )

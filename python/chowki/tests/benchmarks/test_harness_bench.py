"""Proves the benchmark harness and budget assertion machinery work."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from budgets import BUDGETS


@pytest.mark.benchmark
def test_budget_registry_is_complete() -> None:
    """Every hot-path budget named in docs/plans/01-foundation.md must be registered."""
    required = {
        "redaction_1mb_ms",
        "encode_1mb_ms",
        "canonical_hash_1mb_ms",
        "encrypt_1mb_ms",
        "dispatch_ms",
        "snapshot_total_1mb_ms",
        "delta_diff_1mb_ms",
        "warm_resume_base_plus_10_deltas_ms",
        "step_decorator_overhead_us",
        "loop_detect_step_us",
        "budget_track_step_us",
    }
    assert required <= set(BUDGETS)


@pytest.mark.benchmark
def test_harness_measures_and_asserts(benchmark: Any, assert_budget: Any) -> None:
    payload = b"x" * 1024
    result: str = benchmark(lambda: hashlib.sha256(payload).hexdigest())
    assert len(result) == 64
    # 1 KiB SHA-256 must be far under the 1 MiB canonical-hash budget.
    assert_budget(benchmark, "canonical_hash_1mb_ms")

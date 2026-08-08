from __future__ import annotations

from typing import Any

import pytest

from chowki.state.canonical import hash_bytes


@pytest.mark.benchmark
def test_hash_1mib_within_budget(benchmark: Any, assert_budget: Any) -> None:
    payload = b"c" * 1_048_576
    benchmark(hash_bytes, payload)
    assert_budget(benchmark, "canonical_hash_1mb_ms")

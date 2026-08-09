from __future__ import annotations

from typing import Any

import pytest

from chowki.state.crypto import KeyRing, encrypt


@pytest.mark.benchmark
def test_encrypt_1mib_within_budget(benchmark: Any, assert_budget: Any) -> None:
    ring = KeyRing.from_key(b"k" * 32, key_id="k1")
    payload = b"p" * 1_048_576
    benchmark(encrypt, payload, ring, aad=b"tenant:run:v1")
    assert_budget(benchmark, "encrypt_1mb_ms")

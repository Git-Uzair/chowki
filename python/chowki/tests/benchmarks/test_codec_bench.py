from __future__ import annotations

from typing import Any

import pytest

from chowki.state.codec import encode_state


@pytest.mark.benchmark
def test_encode_1mib_within_budget(benchmark: Any, assert_budget: Any) -> None:
    unit = {"role": "assistant", "content": "x" * 400}
    state = {"messages": [dict(unit) for _ in range(2400)]}
    encoded: bytes = benchmark(encode_state, state)
    assert len(encoded) > 900_000
    assert_budget(benchmark, "encode_1mb_ms")

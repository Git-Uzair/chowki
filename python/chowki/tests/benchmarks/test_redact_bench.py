# python/chowki/tests/benchmarks/test_redact_bench.py
from __future__ import annotations

import json
from typing import Any

import pytest

from chowki.state.redact import Redactor


def _one_mib_state() -> dict[str, object]:
    """~1 MiB of realistic agent state: a long message history, no secrets."""
    message = {"role": "assistant", "content": "The analysis shows a stable trend. " * 12}
    unit = len(json.dumps(message).encode())
    return {"messages": [dict(message) for _ in range(1_048_576 // unit)]}


@pytest.mark.benchmark
def test_redact_1mib_within_budget(benchmark: Any, assert_budget: Any) -> None:
    state = _one_mib_state()
    redactor = Redactor(hmac_key=b"bench")
    benchmark(redactor.redact, state)
    assert_budget(benchmark, "redaction_1mb_ms")

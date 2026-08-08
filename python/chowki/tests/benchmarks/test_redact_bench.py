# python/chowki/tests/benchmarks/test_redact_bench.py
from __future__ import annotations

import json
from typing import Any

import pytest

from chowki.state.redact import Redactor


def _one_mib_state() -> dict[str, object]:
    """~1 MiB of realistic agent state: a long message history, no secrets."""
    units = [
        "The analysis shows a stable trend in performance metrics. ",
        "System resource utilization remains optimal across all nodes. ",
        "Configured parameters have been verified and validated cleanly. ",
        "Audit log records indicate normal expected operational status. ",
    ]
    history: list[dict[str, str]] = []
    total_bytes = 0
    i = 0
    while total_bytes < 1_048_576:
        content = units[i % len(units)] * 10
        msg = {"role": "assistant" if i % 2 == 0 else "user", "content": content}
        history.append(msg)
        total_bytes += len(json.dumps(msg).encode())
        i += 1
    return {"messages": history}


@pytest.mark.benchmark
def test_redact_1mib_within_budget(benchmark: Any, assert_budget: Any) -> None:
    state = _one_mib_state()

    def _run() -> None:
        redactor = Redactor(hmac_key=b"bench")
        redactor.redact(state)

    benchmark(_run)
    assert_budget(benchmark, "redaction_1mb_ms")

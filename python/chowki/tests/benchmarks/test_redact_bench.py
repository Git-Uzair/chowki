# python/chowki/tests/benchmarks/test_redact_bench.py
from __future__ import annotations

from typing import Any

import pytest

from chowki.state.redact import Redactor


def _one_mib_state() -> dict[str, object]:
    """~1 MiB of realistic agent state with varying string content, no secrets."""
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
def test_redact_1mib_within_budget(benchmark: Any, assert_budget: Any) -> None:
    state = _one_mib_state()
    redactor = Redactor(hmac_key=b"bench")
    benchmark(redactor.redact, state)
    assert_budget(benchmark, "redaction_1mb_ms")

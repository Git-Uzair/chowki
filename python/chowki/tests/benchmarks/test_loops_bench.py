from __future__ import annotations

from typing import Any

import pytest

from chowki.guardrails.config import GuardrailConfig
from chowki.guardrails.loops import LoopDetector


@pytest.mark.benchmark
def test_loop_detection_per_step_within_budget(benchmark: Any, assert_budget: Any) -> None:
    d = LoopDetector(GuardrailConfig(max_steps_per_run=10**9))
    counter = {"i": 0}

    def _record() -> None:
        counter["i"] += 1
        d.record("search", {"q": f"unique-{counter['i']}"})

    benchmark(_record)
    assert_budget(benchmark, "loop_detect_step_us")

from __future__ import annotations

import pytest

from chowki.errors import InfiniteLoopDetected
from chowki.guardrails.config import GuardrailConfig
from chowki.guardrails.loops import LoopDetector, normalized_levenshtein


def test_identical_tool_calls_trip_the_windowed_hash() -> None:
    d = LoopDetector(GuardrailConfig())
    for _ in range(2):
        d.record("search", {"q": "python"})
    with pytest.raises(InfiniteLoopDetected, match="repeat"):
        d.record("search", {"q": "python"})


def test_key_order_does_not_hide_a_duplicate() -> None:
    d = LoopDetector(GuardrailConfig())
    d.record("t", {"a": 1, "b": 2})
    d.record("t", {"b": 2, "a": 1})
    with pytest.raises(InfiniteLoopDetected):
        d.record("t", {"a": 1, "b": 2})


def test_distinct_calls_do_not_trip() -> None:
    d = LoopDetector(GuardrailConfig())
    for i in range(20):
        d.record("search", {"q": f"query-{i}"})


def test_the_window_slides() -> None:
    """Two duplicates separated by enough distinct calls must not trip."""
    d = LoopDetector(GuardrailConfig())
    d.record("t", {"q": "x"})
    for i in range(5):
        d.record("t", {"q": f"other-{i}"})
    d.record("t", {"q": "x"})
    d.record("t", {"q": "x"})  # only 2 in window -> still fine


def test_max_steps_per_run_is_enforced() -> None:
    d = LoopDetector(GuardrailConfig(max_steps_per_run=3))
    for i in range(3):
        d.record("t", {"q": i})
    with pytest.raises(InfiniteLoopDetected, match="max_steps_per_run"):
        d.record("t", {"q": 99})


def test_normalized_levenshtein_bounds() -> None:
    assert normalized_levenshtein("", "") == 1.0
    assert normalized_levenshtein("abc", "abc") == 1.0
    assert normalized_levenshtein("abc", "xyz") == 0.0
    assert 0.5 < normalized_levenshtein("kitten", "sitting") < 0.7


def test_near_duplicate_prompts_trip_the_semantic_tier() -> None:
    d = LoopDetector(GuardrailConfig())
    base = "Search the internal wiki for the deployment runbook, revision "
    with pytest.raises(InfiniteLoopDetected, match="similarity"):
        for i in range(4):
            d.record_text(f"{base}{i}")


def test_warning_threshold_does_not_raise() -> None:
    d = LoopDetector(GuardrailConfig(semantic_loop_pause_threshold=0.999))
    base = "Search the internal wiki for the deployment runbook, revision "
    for i in range(4):
        d.record_text(f"{base}{i}")
    assert d.warnings, "a 0.85-similarity streak must still emit a warning"


def test_two_node_ping_pong_is_detected() -> None:
    d = LoopDetector(GuardrailConfig())
    with pytest.raises(InfiniteLoopDetected, match="cycle"):
        for _ in range(3):
            d.record_transition("agent_a", "agent_b")
            d.record_transition("agent_b", "agent_a")


def test_three_node_cycle_is_detected() -> None:
    d = LoopDetector(GuardrailConfig())
    with pytest.raises(InfiniteLoopDetected, match="cycle"):
        for _ in range(3):
            d.record_transition("a", "b")
            d.record_transition("b", "c")
            d.record_transition("c", "a")


def test_a_linear_delegation_chain_is_not_a_cycle() -> None:
    d = LoopDetector(GuardrailConfig())
    for src, dst in [("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")]:
        d.record_transition(src, dst)


def test_detector_can_be_disabled() -> None:
    d = LoopDetector(GuardrailConfig(enabled=False))
    for _ in range(100):
        d.record("search", {"q": "same"})


def test_distinct_tools_with_same_sha256_kwargs_do_not_collide() -> None:
    d = LoopDetector(GuardrailConfig())
    # tool_loop_max_repeats is default 3
    d.record("alpha", "sha256:hash1")
    d.record("alpha", "sha256:hash1")
    # Calling beta with same payload should not count toward alpha's repeats
    d.record("beta", "sha256:hash1")
    d.record("beta", "sha256:hash1")


def test_semantic_loop_consecutive_less_than_two() -> None:
    for val in (0, 1):
        d = LoopDetector(GuardrailConfig(semantic_loop_consecutive=val))
        d.record_text("hello")
        d.record_text("world")


def test_cycle_detection_large_acyclic_chain() -> None:
    d = LoopDetector(GuardrailConfig())
    # Create a chain of 1500 nodes where edges repeat twice
    for i in range(1500):
        src, dst = f"node_{i}", f"node_{i + 1}"
        d.record_transition(src, dst)
        d.record_transition(src, dst)


def test_public_record_text_feeds_the_semantic_tier_of_the_current_run() -> None:
    """chowki.record_text is the supported way for applications to feed prompts to
    tier 2 without reaching into run internals. Caught inside the body so the
    workflow itself completes."""
    import chowki
    from chowki.config import ChowkiConfig, ChowkiEngine
    from chowki.storage.memory import MemoryStorage

    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), resume_secret=b"s" * 32))

    @chowki.workflow(engine=engine)
    def chatty() -> str:
        with pytest.raises(InfiniteLoopDetected, match="similarity"):
            for _ in range(5):
                chowki.record_text("please try the exact same thing again " * 3)
        return "caught"

    assert chatty(run_id="lt") == "caught"
    engine.close()


def test_public_record_transition_feeds_the_graph_tier_of_the_current_run() -> None:
    import chowki
    from chowki.config import ChowkiConfig, ChowkiEngine
    from chowki.storage.memory import MemoryStorage

    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), resume_secret=b"s" * 32))

    @chowki.workflow(engine=engine)
    def delegating() -> str:
        with pytest.raises(InfiniteLoopDetected, match="cycle"):
            for _ in range(3):
                chowki.record_transition("planner", "researcher")
                chowki.record_transition("researcher", "planner")
        return "caught"

    assert delegating(run_id="lg") == "caught"
    engine.close()


def test_public_loop_helpers_require_an_active_run() -> None:
    import chowki

    with pytest.raises(LookupError):
        chowki.record_text("outside any run")
    with pytest.raises(LookupError):
        chowki.record_transition("a", "b")

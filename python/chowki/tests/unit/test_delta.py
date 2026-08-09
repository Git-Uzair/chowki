from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from chowki.state.delta import (
    MAX_DELTA_CHAIN,
    DeltaChain,
    apply_patch,
    make_patch,
    should_compact,
)


def test_patch_captures_an_appended_message() -> None:
    before = {"messages": [{"role": "user", "content": "hi"}], "step": 1}
    after = {
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "step": 2,
    }
    patch = make_patch(before, after)
    assert patch
    assert apply_patch(before, patch) == after


def test_apply_patch_does_not_mutate_the_base() -> None:
    before = {"a": 1}
    after = {"a": 2}
    patch = make_patch(before, after)
    result = apply_patch(before, patch)
    assert before == {"a": 1}
    assert result == after


def test_empty_patch_for_identical_states() -> None:
    state = {"a": [1, 2, 3]}
    assert make_patch(state, dict(state)) == []


def test_delta_is_far_smaller_than_a_full_dump() -> None:
    """ADR-002 target: >75% reduction. Assert an order of magnitude, conservatively."""
    from chowki.state.codec import encode_state

    base = {"messages": [{"role": "user", "content": "c" * 200} for _ in range(500)]}
    after = {"messages": [*base["messages"], {"role": "assistant", "content": "ok"}]}
    patch = make_patch(base, after)
    assert len(encode_state(patch)) < len(encode_state(after)) * 0.05


def test_test_op_guards_optimistic_concurrency() -> None:
    from chowki.errors import ChowkiStateError

    base = {"status": "PENDING"}
    guarded = [
        {"op": "test", "path": "/status", "value": "PENDING"},
        {"op": "replace", "path": "/status", "value": "APPROVED"},
    ]
    assert apply_patch(base, guarded) == {"status": "APPROVED"}

    with pytest.raises(ChowkiStateError):
        apply_patch({"status": "CHANGED"}, guarded)


def test_chain_reconstructs_state() -> None:
    chain = DeltaChain(base={"n": 0})
    expected = {"n": 0}
    for i in range(1, 11):
        nxt = {"n": i}
        chain.append(make_patch(expected, nxt))
        expected = nxt
    assert chain.materialize() == {"n": 10}
    assert chain.depth == 10


def test_compaction_triggers_at_depth_50() -> None:
    assert MAX_DELTA_CHAIN == 50
    assert should_compact(depth=49, delta_bytes=0, base_bytes=1_000_000) is False
    assert should_compact(depth=50, delta_bytes=0, base_bytes=1_000_000) is True


def test_compaction_triggers_at_20_percent_cumulative_delta() -> None:
    assert should_compact(depth=3, delta_bytes=199_999, base_bytes=1_000_000) is False
    assert should_compact(depth=3, delta_bytes=200_001, base_bytes=1_000_000) is True


def test_delta_chain_needs_compaction() -> None:
    chain = DeltaChain(base={"a": 1})
    assert chain.needs_compaction(base_bytes=1000) is False


@given(
    st.dictionaries(st.text(min_size=1, max_size=6), st.integers(-100, 100), max_size=8),
    st.dictionaries(st.text(min_size=1, max_size=6), st.integers(-100, 100), max_size=8),
)
def test_patch_roundtrip_property(before: dict[str, int], after: dict[str, int]) -> None:
    assert apply_patch(before, make_patch(before, after)) == after

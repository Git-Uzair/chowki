from __future__ import annotations

import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from chowki.state.canonical import canonicalize, content_hash, hash_bytes


def test_key_order_does_not_change_the_hash() -> None:
    a = {"b": 1, "a": {"z": [1, 2], "y": "x"}}
    b = {"a": {"y": "x", "z": [1, 2]}, "b": 1}
    assert canonicalize(a) == canonicalize(b)
    assert content_hash(a) == content_hash(b)


def test_canonical_form_is_compact_utf8() -> None:
    assert canonicalize({"a": 1, "b": "é"}) == b'{"a":1,"b":"\xc3\xa9"}'


def test_unicode_is_nfc_normalised() -> None:
    """U+00E9 and U+0065 U+0301 are the same character; they must hash alike."""
    assert content_hash({"k": "caf\u00e9"}) == content_hash({"k": "cafe\u0301"})


def test_hash_prefix_and_length() -> None:
    digest = content_hash({"a": 1})
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_hash_bytes_matches_hashlib() -> None:
    assert hash_bytes(b"chowki") == "sha256:" + hashlib.sha256(b"chowki").hexdigest()


def test_non_finite_floats_are_rejected() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="non-finite"):
            canonicalize({"x": bad})


def test_non_bmp_keys_sort_by_utf16_code_units() -> None:
    """RFC 8785 sorts by UTF-16 code units; Python's sorted() uses code points.
    These disagree for astral-plane keys, so the slow path must engage."""
    value = {"\U0001f600": 1, "\uffff": 2}
    out = canonicalize(value).decode()
    assert out.index('"\U0001f600"') < out.index('"\uffff"')


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers(-(10**9), 10**9) | st.text(max_size=20),
        lambda children: (
            st.lists(children, max_size=5)
            | st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=5)
        ),
        max_leaves=20,
    )
)
def test_canonicalize_is_deterministic(value: object) -> None:
    assert canonicalize(value) == canonicalize(value)
    assert content_hash(value) == content_hash(value)

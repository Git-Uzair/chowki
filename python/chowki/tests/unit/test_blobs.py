from __future__ import annotations

import pytest

from chowki.errors import SnapshotIntegrityError
from chowki.state.blobs import BLOB_REF_PREFIX, BlobStore, extract_blobs, inline_blobs


def test_large_values_are_extracted_and_restored() -> None:
    store = BlobStore()
    state = {"system_prompt": "S" * 5000, "small": "ok", "nested": {"tools": ["T" * 9000]}}
    stripped = extract_blobs(state, store, threshold_bytes=4096)

    assert stripped["small"] == "ok"
    assert isinstance(stripped["system_prompt"], str)
    assert stripped["system_prompt"].startswith(BLOB_REF_PREFIX)
    assert stripped["nested"]["tools"][0].startswith(BLOB_REF_PREFIX)
    assert inline_blobs(stripped, store) == state


def test_identical_blobs_deduplicate() -> None:
    store = BlobStore()
    prompt = "P" * 8000
    extract_blobs({"a": prompt}, store, threshold_bytes=4096)
    extract_blobs({"b": prompt}, store, threshold_bytes=4096)
    assert len(store) == 1


def test_missing_blob_raises_rather_than_silently_returning_the_ref() -> None:
    store = BlobStore()
    stripped = extract_blobs({"a": "X" * 9000}, store, threshold_bytes=4096)
    store.clear()
    with pytest.raises(SnapshotIntegrityError):
        inline_blobs(stripped, store)


def test_a_string_that_looks_like_a_ref_is_escaped() -> None:
    """User data must never be mistaken for a chowki blob reference."""
    store = BlobStore()
    hostile = BLOB_REF_PREFIX + "0" * 64
    stripped = extract_blobs({"a": hostile}, store, threshold_bytes=4096)
    assert inline_blobs(stripped, store) == {"a": hostile}


def test_escape_prefix_string_round_trips() -> None:
    """User data starting with ESCAPE_PREFIX (ref-lit:) must round-trip accurately."""
    store = BlobStore()
    literal_ref = "ref-lit:hello"
    stripped = extract_blobs({"a": literal_ref}, store, threshold_bytes=4096)
    assert inline_blobs(stripped, store) == {"a": literal_ref}


def test_lone_surrogates_handling() -> None:
    """Strings containing lone surrogates do not raise UnicodeEncodeError and round-trip."""
    store = BlobStore()
    surrogate_small = "hello_\ud800_world"
    surrogate_large = "\ud800" + "X" * 5000

    stripped_small = extract_blobs({"a": surrogate_small}, store, threshold_bytes=4096)
    assert inline_blobs(stripped_small, store) == {"a": surrogate_small}

    stripped_large = extract_blobs({"a": surrogate_large}, store, threshold_bytes=4096)
    assert inline_blobs(stripped_large, store) == {"a": surrogate_large}

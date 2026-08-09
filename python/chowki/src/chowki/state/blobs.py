"""Content-addressed blob store and >4 KB extraction rules (ADR-002)."""

from __future__ import annotations

import hashlib
from typing import Any, Final, cast

from chowki.errors import SnapshotIntegrityError

BLOB_REF_PREFIX: Final[str] = "ref:sha256:"
ESCAPE_PREFIX: Final[str] = "ref-lit:"


def make_blob_ref(data: bytes) -> str:
    """Derive the content-addressed reference for bytes.

    The single definition of blob addressing, shared by :class:`BlobStore` and the
    storage adapters so every backend agrees on the reference for the same bytes.
    """
    return BLOB_REF_PREFIX + hashlib.sha256(data).hexdigest()


class BlobStore:
    """In-memory key-value blob store mapping content references to bytes."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        """Store bytes and return content-addressed reference string."""
        ref = make_blob_ref(data)
        self._store[ref] = data
        return ref

    def get(self, ref: str) -> bytes:
        """Retrieve stored bytes for reference, raising SnapshotIntegrityError if missing."""
        if ref not in self._store:
            raise SnapshotIntegrityError(f"missing blob {ref}")
        return self._store[ref]

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, ref: object) -> bool:
        return ref in self._store

    def clear(self) -> None:
        self._store.clear()


def extract_string(value: str, store: BlobStore, *, threshold_bytes: int = 4096) -> str:
    """Escape or blob-extract one string leaf.

    Split out of :func:`extract_blobs` so the snapshot pipeline can apply it inside the
    redaction walk instead of paying for a second full traversal of the state tree.
    """
    if value.startswith("ref") and value.startswith((BLOB_REF_PREFIX, ESCAPE_PREFIX)):
        return ESCAPE_PREFIX + value
    # A code point encodes to at most 4 UTF-8 bytes, so shorter strings cannot cross
    # the threshold and never need encoding to find out.
    if len(value) <= threshold_bytes // 4:
        return value
    data = value.encode("utf-8", errors="surrogatepass")
    if len(data) > threshold_bytes:
        return store.put(data)
    return value


def extract_blobs(value: object, store: BlobStore, *, threshold_bytes: int = 4096) -> Any:
    """Extract string leaves exceeding threshold_bytes into store and replace with refs.

    Containers are always rebuilt, so the result shares no mutable object with `value`.

    # TODO(phase-2): extract large sub-objects, not only strings
    """
    if isinstance(value, str):
        return extract_string(value, store, threshold_bytes=threshold_bytes)
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        return {k: extract_blobs(v, store, threshold_bytes=threshold_bytes) for k, v in d.items()}
    if isinstance(value, list):
        lst = cast(list[object], value)
        return [extract_blobs(x, store, threshold_bytes=threshold_bytes) for x in lst]
    if isinstance(value, tuple):
        tpl = cast(tuple[object, ...], value)
        return tuple(extract_blobs(x, store, threshold_bytes=threshold_bytes) for x in tpl)
    return value


def inline_blobs(value: object, store: BlobStore) -> Any:
    """Replace blob reference strings with original string content from store."""
    if isinstance(value, str):
        if value.startswith(ESCAPE_PREFIX):
            return value[len(ESCAPE_PREFIX) :]
        if value.startswith(BLOB_REF_PREFIX):
            return store.get(value).decode("utf-8", errors="surrogatepass")
        return value
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        return {k: inline_blobs(v, store) for k, v in d.items()}
    if isinstance(value, list):
        lst = cast(list[object], value)
        return [inline_blobs(x, store) for x in lst]
    if isinstance(value, tuple):
        tpl = cast(tuple[object, ...], value)
        return tuple(inline_blobs(x, store) for x in tpl)
    return value

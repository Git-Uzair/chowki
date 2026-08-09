"""Chowki state management subpackage."""

from __future__ import annotations

from chowki.state.blobs import (
    BLOB_REF_PREFIX,
    ESCAPE_PREFIX,
    BlobStore,
    extract_blobs,
    inline_blobs,
)
from chowki.state.canonical import (
    canonicalize,
    content_hash,
    hash_bytes,
)
from chowki.state.delta import (
    COMPACT_RATIO,
    MAX_DELTA_CHAIN,
    DeltaChain,
    Patch,
    apply_patch,
    make_patch,
    should_compact,
)
from chowki.state.pipeline import SnapshotPipeline

__all__ = [
    "BLOB_REF_PREFIX",
    "COMPACT_RATIO",
    "ESCAPE_PREFIX",
    "MAX_DELTA_CHAIN",
    "BlobStore",
    "DeltaChain",
    "Patch",
    "SnapshotPipeline",
    "apply_patch",
    "canonicalize",
    "content_hash",
    "extract_blobs",
    "hash_bytes",
    "inline_blobs",
    "make_patch",
    "should_compact",
]

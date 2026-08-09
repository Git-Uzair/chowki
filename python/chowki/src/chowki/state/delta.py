"""RFC 6902 JSON Patch state delta engine and compaction policy.

This module provides diff generation, patch application, and state reconstruction
via RFC 6902 JSON Patch.

Operations emitted:
  Chowki generates patches containing 'add', 'remove', and 'replace' operations
  (as emitted by `jsonpatch.make_patch`).

Operations accepted:
  Chowki accepts all six RFC 6902 operations on input:
  'add', 'remove', 'replace', 'move', 'copy', and 'test'.
  Human-submitted patches from human-in-the-loop (HITL) gateways may legitimately
  use 'test', 'move', and 'copy' operations.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Final, cast

import jsonpatch  # type: ignore[import-untyped]
import jsonpointer  # type: ignore[import-untyped]

from chowki.errors import ChowkiStateError
from chowki.state.codec import encode_state
from chowki.types import JSONValue

_jsonpatch: Any = jsonpatch

#: jsonpointer.JsonPointerException is a sibling of JsonPatchException, not a subclass;
#: an unresolvable path (e.g. "/x/y" on {"a": 1}) surfaces it through the jsonpatch API.
_PATCH_ERRORS: Final[tuple[type[Exception], ...]] = (
    jsonpatch.JsonPatchException,
    jsonpointer.JsonPointerException,
)

Patch = list[dict[str, Any]]

MAX_DELTA_CHAIN: Final[int] = 50
COMPACT_RATIO: Final[float] = 0.20


def make_patch(before: JSONValue, after: JSONValue) -> Patch:
    """Generate an RFC 6902 JSON Patch representing the diff from `before` to `after`."""
    jp: Any = _jsonpatch.make_patch(before, after)
    return cast(Patch, jp.patch)


def apply_patch(base: JSONValue, patch: Patch, *, in_place: bool = False) -> JSONValue:
    """Apply an RFC 6902 JSON Patch to state `base`.

    Raises ChowkiStateError if a patch operation fails (e.g., test operation mismatch
    or invalid path conflict).
    """
    try:
        res: Any = _jsonpatch.apply_patch(base, patch, in_place=in_place)
        return cast(JSONValue, res)
    except _PATCH_ERRORS as e:
        failing_op = _find_failing_op(base, patch)
        raise ChowkiStateError(f"Failed to apply patch operation {failing_op}: {e}") from e


def _find_failing_op(base: JSONValue, patch: Patch) -> dict[str, Any] | None:
    curr: Any = copy.deepcopy(base)
    for op in patch:
        try:
            curr = _jsonpatch.apply_patch(curr, [op], in_place=True)
        except _PATCH_ERRORS:
            return op
    return None


def should_compact(*, depth: int, delta_bytes: int, base_bytes: int) -> bool:
    """Determine if a delta chain should be compacted into a new base snapshot."""
    return depth >= MAX_DELTA_CHAIN or (base_bytes > 0 and delta_bytes > base_bytes * COMPACT_RATIO)


@dataclass(slots=True)
class DeltaChain:
    """In-memory delta chain tracking base state and sequence of diff patches."""

    base: JSONValue
    patches: list[Patch] = field(default_factory=list[Patch])
    delta_bytes: int = 0

    def __post_init__(self) -> None:
        if self.patches and self.delta_bytes == 0:
            self.delta_bytes = sum(len(encode_state(p)) for p in self.patches)

    @property
    def depth(self) -> int:
        return len(self.patches)

    def append(self, patch: Patch) -> None:
        """Append a patch to the chain and accumulate delta byte size."""
        self.patches.append(patch)
        self.delta_bytes += len(encode_state(patch))

    def materialize(self) -> JSONValue:
        """Reconstruct state by sequentially applying patches to the base snapshot.

        Uses in_place=False for the first patch (creating a private copy),
        and in_place=True for intermediate steps to optimize performance.
        """
        if not self.patches:
            return copy.deepcopy(self.base)

        curr = apply_patch(self.base, self.patches[0], in_place=False)
        for patch in self.patches[1:]:
            curr = apply_patch(curr, patch, in_place=True)
        return curr

    def needs_compaction(self, base_bytes: int) -> bool:
        """Check if the chain meets compaction thresholds given base state byte size."""
        return should_compact(depth=self.depth, delta_bytes=self.delta_bytes, base_bytes=base_bytes)

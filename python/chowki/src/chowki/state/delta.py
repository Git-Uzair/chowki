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

#: The operations the fast path handles itself. Everything else -- `move`, `copy`, `test`,
#: deeper paths, escaped tokens -- is handed to `jsonpatch`, which owns RFC 6902 in full.
_FAST_OPS: Final[frozenset[str]] = frozenset({"add", "replace", "remove"})


def make_patch(before: JSONValue, after: JSONValue) -> Patch:
    """Generate an RFC 6902 JSON Patch representing the diff from `before` to `after`."""
    jp: Any = _jsonpatch.make_patch(before, after)
    return cast(Patch, jp.patch)


def _array_index(member: str) -> int | None:
    """Parse an RFC 6901 array index: ASCII digits, no leading zero. None if it is not one."""
    if not member.isascii() or not member.isdigit():
        return None
    if len(member) > 1 and member[0] == "0":
        return None
    return int(member)


def _try_fast_patch(work: dict[str, Any], patch: Patch) -> bool:
    """Apply one- and two-segment add/replace/remove operations to `work` directly.

    Each operation is validated against `work` immediately before it is applied, never
    up front: RFC 6902 is defined by sequential application, so a precondition checked
    against the pre-patch document is stale by construction. `[remove /a, remove /a]`
    and `[remove /items/0, add /items/3]` are conflicts, and only the current working
    document can say so.

    A `False` return means "not handled here", not "invalid": the caller re-applies the
    whole patch with `jsonpatch`, which owns conflict semantics and raises. `work` is the
    caller's throwaway copy, so the operations already applied are simply discarded.
    Containers reached from `work` are copied before their first mutation, because `work`
    is a shallow copy that shares them with the caller's `base`.
    """
    copied: set[str] = set()
    for op_dict in patch:
        if not isinstance(cast(Any, op_dict), dict):
            return False
        op = op_dict.get("op")
        path = op_dict.get("path")
        if op not in _FAST_OPS or not isinstance(path, str) or "~" in path:
            return False
        if op != "remove" and "value" not in op_dict:
            return False

        # "/a" -> ["", "a"] and "/a/b" -> ["", "a", "b"]; anything else, jsonpatch's.
        parts = path.split("/")
        if parts[0] != "" or len(parts) not in (2, 3) or not all(parts[1:]):
            return False

        if len(parts) == 2:
            key = parts[1]
            if op == "remove":
                if key not in work:
                    return False
                del work[key]
            else:
                work[key] = op_dict["value"]
            continue

        parent_key, member = parts[1], parts[2]
        if parent_key not in work:
            return False
        parent = work[parent_key]

        if isinstance(parent, dict):
            d_parent = cast(dict[str, Any], parent)
            if parent_key not in copied:
                d_parent = dict(d_parent)
                work[parent_key] = d_parent
                copied.add(parent_key)
            if op == "remove":
                if member not in d_parent:
                    return False
                del d_parent[member]
            else:
                d_parent[member] = op_dict["value"]
        elif isinstance(parent, list):
            l_parent = cast(list[object], parent)
            if parent_key not in copied:
                l_parent = list(l_parent)
                work[parent_key] = l_parent
                copied.add(parent_key)
            if member == "-":
                if op != "add":
                    return False
                l_parent.append(op_dict["value"])
                continue
            idx = _array_index(member)
            # `add` may address one past the end; `replace` and `remove` may not.
            if idx is None or idx > (len(l_parent) if op == "add" else len(l_parent) - 1):
                return False
            if op == "add":
                l_parent.insert(idx, op_dict["value"])
            elif op == "replace":
                l_parent[idx] = op_dict["value"]
            else:
                del l_parent[idx]
        else:
            return False

    return True


def apply_patch(base: JSONValue, patch: Patch, *, in_place: bool = False) -> JSONValue:
    """Apply an RFC 6902 JSON Patch to state `base`.

    Raises ChowkiStateError if a patch operation fails (e.g., test operation mismatch
    or invalid path conflict).
    """
    if not isinstance(cast(Any, patch), list):
        raise ChowkiStateError(f"Invalid patch: expected list, got {type(patch).__name__}")
    for op_dict in patch:
        if not isinstance(cast(Any, op_dict), dict):
            raise ChowkiStateError(
                f"Invalid patch operation: expected dict, got {type(op_dict).__name__}"
            )

    try:
        if isinstance(base, dict):
            # A throwaway copy even when `in_place`: the fast path mutates as it goes and
            # may still bail on a later operation, and the jsonpatch fallback has to see
            # a document nothing has touched.
            work: dict[str, Any] = dict(base)
            if _try_fast_patch(work, patch):
                if in_place:
                    base.clear()
                    base.update(work)
                    return base
                return work

        target = base if in_place else copy.deepcopy(base)
        res: Any = _jsonpatch.apply_patch(target, patch, in_place=True)
        return cast(JSONValue, res)
    except _PATCH_ERRORS as e:
        failing_op = _find_failing_op(base, patch)
        raise ChowkiStateError(f"Failed to apply patch operation {failing_op}: {e}") from e
    except ChowkiStateError:
        raise
    except Exception as e:
        failing_op = _find_failing_op(base, patch)
        raise ChowkiStateError(f"Failed to apply patch operation {failing_op}: {e}") from e


def _find_failing_op(base: JSONValue, patch: Patch) -> dict[str, Any] | None:
    curr: Any = copy.deepcopy(base)
    for op in patch:
        if not isinstance(cast(Any, op), dict):
            return None
        try:
            curr = _jsonpatch.apply_patch(curr, [op], in_place=True)
        except _PATCH_ERRORS:
            return op
        except Exception:
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
        """Reconstruct state by sequentially applying patches to the base snapshot."""
        if not self.patches:
            return copy.deepcopy(self.base)

        curr = apply_patch(self.base, self.patches[0], in_place=False)
        for patch in self.patches[1:]:
            curr = apply_patch(curr, patch, in_place=False)
        return curr

    def needs_compaction(self, base_bytes: int) -> bool:
        """Check if the chain meets compaction thresholds given base state byte size."""
        return should_compact(depth=self.depth, delta_bytes=self.delta_bytes, base_bytes=base_bytes)

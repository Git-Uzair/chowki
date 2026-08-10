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


def _try_fast_patch(curr: Any, patch: Patch, *, in_place: bool = False) -> bool:
    """Attempt to apply simple JSON Patch operations directly in-place.

    Returns True if all operations were successfully applied, False if a complex
    operation requires fallback to full jsonpatch engine.
    """
    if not isinstance(curr, dict):
        return False

    # Pass 1: Validate all operations in patch before making any mutation
    for op_dict in patch:
        if not isinstance(cast(Any, op_dict), dict):
            return False
        op = op_dict.get("op")
        path = op_dict.get("path")
        if not isinstance(op, str) or not isinstance(path, str) or not path.startswith("/"):
            return False
        if op not in ("add", "replace", "remove"):
            return False
        if "~" in path:
            return False

        slash_count = path.count("/")
        if slash_count == 1:
            last_part = path[1:]
            if not last_part:
                return False
            if op in ("add", "replace") and "value" not in op_dict:
                return False
            if op == "remove" and last_part not in curr:
                return False
        elif slash_count == 2 and not path.endswith("/"):
            idx_slash2 = path.rfind("/")
            p_key = path[1:idx_slash2]
            last_part = path[idx_slash2 + 1 :]
            if not p_key or p_key not in curr:
                return False
            d_curr_v = cast(dict[str, Any], curr)
            check_val = d_curr_v[p_key]
            if isinstance(check_val, list):
                l_val = cast(list[object], check_val)
                if op == "add":
                    if "value" not in op_dict:
                        return False
                    if last_part != "-" and (
                        not last_part.isdigit() or int(last_part) < 0 or int(last_part) > len(l_val)
                    ):
                        return False
                elif op == "replace":
                    if (
                        "value" not in op_dict
                        or not last_part.isdigit()
                        or int(last_part) < 0
                        or int(last_part) >= len(l_val)
                    ):
                        return False
                elif op == "remove":
                    if (
                        not last_part.isdigit()
                        or int(last_part) < 0
                        or int(last_part) >= len(l_val)
                    ):
                        return False
            elif isinstance(check_val, dict):
                d_val = cast(dict[str, Any], check_val)
                if op in ("add", "replace") and "value" not in op_dict:
                    return False
                if op == "remove" and last_part not in d_val:
                    return False
            else:
                return False
        else:
            return False

    # Pass 2: Apply operations safely
    d_curr = cast(dict[str, Any], curr)
    copied_keys: set[str] = set()

    for op_dict in patch:
        op = cast(str, op_dict["op"])
        path = cast(str, op_dict["path"])
        slash_count = path.count("/")

        if slash_count == 1:
            last_part = path[1:]
            if op in ("add", "replace"):
                d_curr[last_part] = op_dict["value"]
            elif op == "remove":
                d_curr.pop(last_part, None)
        elif slash_count == 2:
            idx_slash2 = path.rfind("/")
            p_key = path[1:idx_slash2]
            last_part = path[idx_slash2 + 1 :]
            curr_item = d_curr[p_key]

            if isinstance(curr_item, list):
                val_lst = cast(list[object], curr_item)
                if p_key not in copied_keys:
                    val_lst = list(val_lst)
                    d_curr[p_key] = val_lst
                    copied_keys.add(p_key)
                l_val = val_lst
                if op == "add":
                    if last_part == "-":
                        l_val.append(op_dict["value"])
                    else:
                        idx = int(last_part)
                        l_val.insert(idx, op_dict["value"])
                elif op == "replace":
                    idx = int(last_part)
                    l_val[idx] = op_dict["value"]
                elif op == "remove":
                    idx = int(last_part)
                    l_val.pop(idx)

            elif isinstance(curr_item, dict):
                val_dict = cast(dict[str, Any], curr_item)
                if p_key not in copied_keys:
                    val_dict = dict(val_dict)
                    d_curr[p_key] = val_dict
                    copied_keys.add(p_key)
                d_val = val_dict
                if op in ("add", "replace"):
                    d_val[last_part] = op_dict["value"]
                elif op == "remove":
                    d_val.pop(last_part, None)

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

    if in_place:
        curr = base
    else:
        if isinstance(base, dict):
            curr = dict(base)
        elif isinstance(base, list):
            curr = list(base)
        else:
            curr = copy.deepcopy(base)

    try:
        if _try_fast_patch(curr, patch):
            return curr
        fallback_base = base if in_place else copy.deepcopy(base)
        res: Any = _jsonpatch.apply_patch(fallback_base, patch, in_place=in_place)
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

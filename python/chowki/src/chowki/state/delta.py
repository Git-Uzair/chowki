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


def _try_fast_patch(curr: Any, patch: Patch, *, copy_on_write: bool = False) -> bool:
    """Attempt to apply simple JSON Patch operations directly in-place.

    Returns True if all operations were successfully applied, False if a complex
    operation requires fallback to full jsonpatch engine.
    """
    for op_dict in patch:
        op = op_dict.get("op")
        path = op_dict.get("path")
        if not isinstance(op, str) or not isinstance(path, str) or not path.startswith("/"):
            return False

        if op not in ("add", "replace", "remove"):
            return False

        # Fast path for common 2-segment paths (e.g. "/messages/-") without ~ escape
        if "~" not in path:
            slash_count = path.count("/")
            if slash_count == 2 and not path.endswith("/"):
                idx_slash2 = path.rfind("/")
                p_key = path[1:idx_slash2]
                last_part = path[idx_slash2 + 1 :]
                d_curr_fast = cast(dict[str, Any], curr)
                if p_key and isinstance(curr, dict) and p_key in d_curr_fast:
                    val: Any = d_curr_fast[p_key]
                    if copy_on_write:
                        if isinstance(val, dict):
                            val = dict(cast(Any, val))
                        elif isinstance(val, list):
                            val = list(cast(Any, val))
                        d_curr_fast[p_key] = val

                    if isinstance(val, list):
                        l_val = cast(Any, val)
                        if op == "add":
                            if "value" not in op_dict:
                                return False
                            if last_part == "-":
                                l_val.append(op_dict["value"])
                                continue
                            elif last_part.isdigit():
                                idx = int(last_part)
                                if idx < 0 or idx > len(l_val):
                                    return False
                                if idx == len(l_val):
                                    l_val.append(op_dict["value"])
                                else:
                                    l_val.insert(idx, op_dict["value"])
                                continue
                        elif op == "replace":
                            if "value" not in op_dict or not last_part.isdigit():
                                return False
                            idx = int(last_part)
                            if idx < 0 or idx >= len(l_val):
                                return False
                            l_val[idx] = op_dict["value"]
                            continue
                        elif op == "remove":
                            if not last_part.isdigit():
                                return False
                            idx = int(last_part)
                            if idx < 0 or idx >= len(l_val):
                                return False
                            l_val.pop(idx)
                            continue

                    elif isinstance(val, dict):
                        d_val = cast(dict[str, Any], val)
                        if op in ("add", "replace"):
                            if "value" not in op_dict:
                                return False
                            d_val[last_part] = op_dict["value"]
                            continue
                        elif op == "remove":
                            if last_part not in d_val:
                                return False
                            d_val.pop(last_part)
                            continue

            elif slash_count == 1:
                last_part = path[1:]
                if last_part and isinstance(curr, dict):
                    d_curr = cast(dict[str, Any], curr)
                    if op in ("add", "replace"):
                        if "value" not in op_dict:
                            return False
                        d_curr[last_part] = op_dict["value"]
                        continue
                    elif op == "remove":
                        if last_part not in d_curr:
                            return False
                        d_curr.pop(last_part)
                        continue

        parts = path.split("/")[1:]
        if not parts or any(p == "" for p in parts[:-1]):
            return False

        target: Any = cast(Any, curr)
        for part in parts[:-1]:
            if isinstance(target, dict):
                p_key = part.replace("~1", "/").replace("~0", "~")
                d_target = cast(dict[str, Any], target)
                if p_key not in d_target:
                    return False
                val_node: Any = d_target[p_key]
                if copy_on_write:
                    if isinstance(val_node, dict):
                        val_node = dict(cast(Any, val_node))
                    elif isinstance(val_node, list):
                        val_node = list(cast(Any, val_node))
                    d_target[p_key] = val_node
                target = val_node
            elif isinstance(target, list):
                if not part.isdigit():
                    return False
                idx = int(part)
                l_target: Any = cast(Any, target)
                if idx < 0 or idx >= len(l_target):
                    return False
                val_list: Any = l_target[idx]
                if copy_on_write:
                    if isinstance(val_list, dict):
                        val_list = dict(cast(Any, val_list))
                    elif isinstance(val_list, list):
                        val_list = list(cast(Any, val_list))
                    l_target[idx] = val_list
                target = val_list
            else:
                return False

        last_part = parts[-1].replace("~1", "/").replace("~0", "~")

        if isinstance(target, dict):
            dict_target = cast(dict[str, Any], target)
            if op in ("add", "replace"):
                if "value" not in op_dict:
                    return False
                dict_target[last_part] = op_dict["value"]
            elif op == "remove":
                if last_part not in dict_target:
                    return False
                dict_target.pop(last_part)
            else:
                return False

        elif isinstance(target, list):
            list_target: Any = cast(Any, target)
            if op == "add":
                if "value" not in op_dict:
                    return False
                if last_part == "-":
                    list_target.append(op_dict["value"])
                elif last_part.isdigit():
                    idx = int(last_part)
                    if idx < 0 or idx > len(list_target):
                        return False
                    if idx == len(list_target):
                        list_target.append(op_dict["value"])
                    else:
                        list_target.insert(idx, op_dict["value"])
                else:
                    return False
            elif op == "replace":
                if "value" not in op_dict or not last_part.isdigit():
                    return False
                idx = int(last_part)
                if idx < 0 or idx >= len(list_target):
                    return False
                list_target[idx] = op_dict["value"]
            elif op == "remove":
                if not last_part.isdigit():
                    return False
                idx = int(last_part)
                if idx < 0 or idx >= len(list_target):
                    return False
                list_target.pop(idx)
            else:
                return False
        else:
            return False

    return True


def apply_patch(base: JSONValue, patch: Patch, *, in_place: bool = False) -> JSONValue:
    """Apply an RFC 6902 JSON Patch to state `base`.

    Raises ChowkiStateError if a patch operation fails (e.g., test operation mismatch
    or invalid path conflict).
    """
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
        if _try_fast_patch(curr, patch, copy_on_write=not in_place):
            return curr
        fallback_base = base if in_place else copy.deepcopy(base)
        res: Any = _jsonpatch.apply_patch(fallback_base, patch, in_place=in_place)
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

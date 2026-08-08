"""Canonical JSON serialization (RFC 8785 subset) and SHA-256 hashing.

Known deviation from RFC 8785:
Float serialization uses Python's repr via json.dumps, which differs from
ECMAScript Number::toString for extreme magnitudes (e.g. 1e21).
# TODO(phase-2): full ES number formatting when the Node SDK lands
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any, Final, cast

_HASH_PREFIX: Final[str] = "sha256:"


def hash_bytes(data: bytes) -> str:
    """Return SHA-256 digest prefixed with 'sha256:'."""
    return _HASH_PREFIX + hashlib.sha256(data).hexdigest()


def _nfc(value: object) -> Any:
    """Recursively normalize string keys and values to Unicode NFC."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        res: dict[str, Any] = {}
        for k, v in d.items():
            norm_k = unicodedata.normalize("NFC", k)
            if norm_k in res:
                raise ValueError(f"duplicate dict keys after NFC normalization: {k!r}")
            res[norm_k] = _nfc(v)
        return res
    if isinstance(value, list):
        lst = cast(list[object], value)
        return [_nfc(x) for x in lst]
    if isinstance(value, tuple):
        tpl = cast(tuple[object, ...], value)
        return tuple(_nfc(x) for x in tpl)
    return value


def _has_astral_key(value: object) -> bool:
    """Return True if any dict key in value contains an astral character (> U+FFFF)."""
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        for k, v in d.items():
            if any(ord(c) > 0xFFFF for c in k):
                return True
            if _has_astral_key(v):
                return True
    elif isinstance(value, (list, tuple)):
        seq = cast(list[object] | tuple[object, ...], value)
        for x in seq:
            if _has_astral_key(x):
                return True
    return False


def _canonical_astral(value: object) -> Any:
    """Recursively sort dict keys by UTF-16 code unit order (key.encode('utf-16-be'))."""
    if isinstance(value, dict):
        d = cast(dict[str, Any], value)
        sorted_keys = sorted(d.keys(), key=lambda k: k.encode("utf-16-be"))
        return {k: _canonical_astral(d[k]) for k in sorted_keys}
    if isinstance(value, list):
        lst = cast(list[object], value)
        return [_canonical_astral(x) for x in lst]
    if isinstance(value, tuple):
        tpl = cast(tuple[object, ...], value)
        return tuple(_canonical_astral(x) for x in tpl)
    return value


def canonicalize(value: object) -> bytes:
    """Serialize value to canonical JSON bytes according to RFC 8785 rules."""
    normalized = _nfc(value)
    if _has_astral_key(normalized):
        target = _canonical_astral(normalized)
        sort_keys = False
    else:
        target = normalized
        sort_keys = True

    try:
        dumped = json.dumps(
            target,
            sort_keys=sort_keys,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as err:
        raise ValueError("non-finite number in canonical JSON") from err

    return dumped.encode("utf-8")


def content_hash(value: object) -> str:
    """Return 'sha256:<hex>' content hash of canonical JSON for value."""
    return hash_bytes(canonicalize(value))

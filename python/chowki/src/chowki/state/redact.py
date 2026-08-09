# python/chowki/src/chowki/state/redact.py
"""Two-tier secret redaction engine (ADR-003, docs/research/02-serialization.md:289-312).

Layer 1 is a single combined regex alternation over known credential formats.
Layer 2 is Shannon-entropy detection of unknown high-entropy tokens.
A key-name tier redacts whole values under sensitive dict keys before either layer.
"""

# mypy: disable-error-code="redundant-cast"
# The casts in _redact_any are required by pyright strict (Any narrowed via
# isinstance yields Unknown element types) but are no-ops to mypy.

from __future__ import annotations

import hashlib
import hmac
import math
import re
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final, cast, overload

import structlog

from chowki.state.blobs import BlobStore, extract_string
from chowki.types import JSONValue

logger = structlog.get_logger()

__all__ = ["PLACEHOLDER_RE", "Redactor"]

PLACEHOLDER_RE: Final = re.compile(r"\[REDACTED:[a-z0-9_]+:[0-9a-f]{8}\]")

_HAS_DIGIT: Final = re.compile(r"\d")

_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    (
        "private_key",
        r"-----BEGIN[A-Z \-]*PRIVATE KEY-----[\s\S]*?-----END[A-Z \-]*PRIVATE KEY-----",
    ),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
    ("openai_proj", r"\bsk-proj-[A-Za-z0-9\-_]{40,}"),
    ("anthropic", r"\bsk-ant-[A-Za-z0-9\-_]{40,}"),
    # No leading \b and an alphanumeric-only tail: a secret pasted flush against a
    # word ("Ask-A1b2...") must still be caught, while hyphenated prose after "sk-"
    # ("ask-for-the-longer-token") must not. Hyphen/underscore-bearing key formats
    # have their own dedicated patterns above.
    ("openai", r"sk-[A-Za-z0-9]{20,}"),
    ("stripe", r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"),
    ("aws_access", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("aws_secret", r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{20,}"),
    ("github", r"\bghp_[A-Za-z0-9]{36}\b"),
    ("slack", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    # The lookaheads require at least one non-alphabetic token character so plain
    # prose after the scheme word ("Bearer authentication is required") survives.
    ("bearer", r"\bBearer\s+(?=[A-Za-z\-_]*[0-9=+/~.])[A-Za-z0-9\-._~+/]{10,}=*"),
    ("basic", r"\bBasic\s+(?=[A-Za-z]*[0-9+/=])[A-Za-z0-9+/]{10,}={0,2}"),
    ("uri_userinfo", r"(?<=://)[^\s'\"/]*:[^\s'\"@/]+(?=@)"),
)

_DEFAULT_COMBINED_RE: Final = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in _PATTERNS))

_SENSITIVE_KEY: Final = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|auth(?:orization)?|credential|private[_-]?key|access[_-]?key)"
)
_SAFE_KEYS: Final = frozenset(
    {"role", "content", "messages", "type", "name", "id", "text", "user", "system", "assistant"}
)
_SAFE_VALUES: Final = frozenset(
    {"user", "assistant", "system", "tool", "function", "pending", "running", "completed", "failed"}
)

_UUID_RE: Final = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE: Final = re.compile(r"^[0-9a-fA-F]+$")

_CONTAINER_TYPES: Final = (dict, list, tuple)

#: A minimum blob length no string can reach, used as the "no blob store" sentinel.
_NEVER_BLOB: Final = sys.maxsize


def _has_indicator(t: str) -> bool:
    """Cheap C-speed gate: can any layer-1 pattern possibly match ``t``?

    Every pattern in ``_PATTERNS`` guarantees one of the probed literals in its
    match, so a False here proves the combined regex cannot fire. Each probe is
    a single ``str.__contains__`` scan, gated per family on one rare character,
    so benign prose never pays for the full alternation. Deliberately narrow
    probes ("k_live_", not "sk_") keep words like "task_0" from tripping it,
    and longer needles skip further per mismatch.
    """
    if "-" in t and ("sk-" in t or "xox" in t or "-----" in t):
        return True
    if "_" in t and (
        "k_live_" in t or "k_test_" in t or "aws_secret_access_key" in t or "ghp_" in t
    ):
        return True
    if ":" in t and "://" in t:
        return True
    if "J" in t and "eyJ" in t:
        return True
    if "B" in t and ("Bearer" in t or "Basic" in t):
        return True
    return "I" in t and ("AKIA" in t or "ASIA" in t)


def _is_number(s: str) -> bool:
    if not all(c in "0123456789.-+eE" for c in s):
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_safe(token: str) -> bool:
    if "/" in token or "\\" in token:
        return True
    if _HAS_DIGIT.search(token) is None:
        return True
    if len(token) == 36 and _UUID_RE.match(token) is not None:
        return True
    if len(token) in (16, 32, 40, 64) and _HEX_RE.match(token) is not None:
        return True
    if _is_number(token):
        return True
    return token.startswith("REDACTED:")


def _shannon(token: str) -> float:
    length = len(token)
    if length == 0:
        return 0.0
    counts = Counter(token)
    return -sum((cnt / length) * math.log2(cnt / length) for cnt in counts.values())


class Redactor:
    def __init__(
        self,
        *,
        hmac_key: bytes,
        entropy_threshold: float = 4.5,
        min_token_len: int = 12,
        enable_entropy: bool = True,
        entropy_max_scan_bytes: int = 4096,
        extra_patterns: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._hmac_key = hmac_key
        self._entropy_threshold = entropy_threshold
        self._min_token_len = min_token_len
        self._update_candidate_re()
        self.enable_entropy = enable_entropy
        self.entropy_max_scan_bytes = entropy_max_scan_bytes
        self._has_extra_patterns = bool(extra_patterns)
        # Caller-supplied patterns can match anything, including all-letter tokens, so
        # the cheap inert screen in _redact_leaf is switched off when any are configured.
        self._screen = not extra_patterns
        self._entropy_skip_count = 0

        if extra_patterns:
            patterns: list[tuple[str, str]] = list(_PATTERNS)
            for i, (name, pat) in enumerate(extra_patterns):
                clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", name) or "extra"
                patterns.append((f"extra_{i}_{clean_name}", pat))
            combined_pattern = "|".join(f"(?P<{name}>{pat})" for name, pat in patterns)
            self._combined_re = re.compile(combined_pattern)
        else:
            self._combined_re = _DEFAULT_COMBINED_RE

    @property
    def entropy_threshold(self) -> float:
        return self._entropy_threshold

    @entropy_threshold.setter
    def entropy_threshold(self, value: float) -> None:
        self._entropy_threshold = value
        self._update_candidate_re()

    @property
    def min_token_len(self) -> int:
        return self._min_token_len

    @min_token_len.setter
    def min_token_len(self, value: int) -> None:
        self._min_token_len = value
        self._update_candidate_re()

    @property
    def entropy_skip_count(self) -> int:
        """How many oversized strings have skipped the entropy tier so far."""
        return self._entropy_skip_count

    def _update_candidate_re(self) -> None:
        # Shannon entropy of a string is at most log2(len), so a token can only
        # clear the threshold once it has at least ceil(2**threshold) characters.
        min_len = max(self._min_token_len, math.ceil(2**self._entropy_threshold))
        self._min_entropy_len = min_len
        self._candidate_re = re.compile(rf"[A-Za-z0-9+/=_\-.!@#$%&*]{{{min_len},}}")

    def placeholder(self, kind: str, secret: str) -> str:
        short = hmac.new(
            self._hmac_key, secret.encode("utf-8", errors="replace"), hashlib.sha256
        ).hexdigest()[:8]
        kind_clean = re.sub(r"[^a-z0-9_]", "_", kind.lower()) or "secret"
        return f"[REDACTED:{kind_clean}:{short}]"

    def _sub_layer1(self, match: re.Match[str]) -> str:
        kind = match.lastgroup or "secret"
        secret = match.group()
        return self.placeholder(kind, secret)

    def _sub_layer2(self, match: re.Match[str]) -> str:
        token = match.group()
        if len(token) < self._min_entropy_len or _HAS_DIGIT.search(token) is None:
            return token
        if _is_safe(token):
            return token
        if _shannon(token) >= self.entropy_threshold:
            return self.placeholder("entropy", token)
        return token

    def redact_text(self, text: str) -> str:
        if len(text) < 8:
            return text

        placeholders: list[str] = []
        nonce: str | None = None
        if "[REDACTED:" in text and PLACEHOLDER_RE.search(text):
            nonce = uuid.uuid4().hex
            prefix = f"\x00PH_{nonce}_"

            def _mask_ph(m: re.Match[str]) -> str:
                idx = len(placeholders)
                placeholders.append(m.group(0))
                return f"{prefix}{idx}\x00"

            working_text = PLACEHOLDER_RE.sub(_mask_ph, text)
        else:
            working_text = text

        if self._has_extra_patterns or _has_indicator(working_text):
            res = self._combined_re.sub(self._sub_layer1, working_text)
        else:
            res = working_text

        if self.enable_entropy:
            if len(res) > self.entropy_max_scan_bytes:
                self._entropy_skip_count += 1
                if self._entropy_skip_count == 1:
                    logger.debug(
                        "redact_entropy_skipped_large_string",
                        length=len(res),
                        max_bytes=self.entropy_max_scan_bytes,
                        note="further skips counted in entropy_skip_count, not logged",
                    )
            elif (
                not res.isalpha()
                and _HAS_DIGIT.search(res) is not None
                and self._candidate_re.search(res) is not None
            ):
                res = self._candidate_re.sub(self._sub_layer2, res)

        if placeholders and nonce is not None:
            unmask_re = re.compile(rf"\x00PH_{nonce}_(\d+)\x00")
            res = unmask_re.sub(lambda m: placeholders[int(m.group(1))], res)

        return res

    def _redact_leaf(
        self, text: str, store: BlobStore | None, threshold: int, blob_min: int
    ) -> str:
        """Redact one string leaf, then hand it to blob extraction if a store is given.

        Fusing the blob pass into this walk keeps the pipeline's order — redact first,
        so a large secret is replaced by a short placeholder and never becomes a blob —
        while paying for one traversal of the state tree instead of two.

        The screen that skips ``redact_text`` is the hot path of every snapshot, so it
        is spelled out here rather than hidden behind a call. It is sound because layer 1
        cannot fire without one of the six characters ``_has_indicator`` probes for, and
        layer 2 cannot fire without an ASCII digit: its candidate tokens come from an
        ASCII-only class and ``_sub_layer2`` returns any token without a digit untouched.
        Each ``in`` is a single ``memchr`` that stops at the first hit, so a string that
        does need the full scan falls through almost immediately, while inert prose is
        cleared for about a nanosecond per character. Keep these 16 characters in step
        with ``_PATTERNS``; ``test_no_known_secret_is_screened_out_as_inert`` guards it.
        """
        n = len(text)
        if (
            n < 8  # redact_text() is a no-op below 8 characters
            or (n < 10 and text in _SAFE_VALUES)
            or (
                self._screen
                and not (
                    "0" in text
                    or "1" in text
                    or "2" in text
                    or "3" in text
                    or "4" in text
                    or "5" in text
                    or "6" in text
                    or "7" in text
                    or "8" in text
                    or "9" in text
                    or "-" in text
                    or "_" in text
                    or ":" in text
                    or "J" in text
                    or "B" in text
                    or "I" in text
                )
            )
        ):
            # Inert. Nothing that needs the ``ref:``/``ref-lit:`` escape can be inert
            # (both prefixes carry gate characters), so only the blob threshold is left.
            if n <= blob_min:
                return text
            # blob_min is _NEVER_BLOB whenever store is None, so getting here proves one.
            return extract_string(text, cast(BlobStore, store), threshold_bytes=threshold)
        redacted = self.redact_text(text)
        if store is None:
            return redacted
        return extract_string(redacted, store, threshold_bytes=threshold)

    @overload
    def redact(
        self,
        value: dict[str, Any],
        *,
        blobs: BlobStore | None = ...,
        blob_threshold_bytes: int = ...,
    ) -> dict[str, Any]: ...

    @overload
    def redact(
        self, value: list[Any], *, blobs: BlobStore | None = ..., blob_threshold_bytes: int = ...
    ) -> list[Any]: ...

    @overload
    def redact(
        self, value: JSONValue, *, blobs: BlobStore | None = ..., blob_threshold_bytes: int = ...
    ) -> JSONValue: ...

    def redact(
        self, value: Any, *, blobs: BlobStore | None = None, blob_threshold_bytes: int = 4096
    ) -> Any:
        """Return a redacted copy of ``value``.

        Containers are always rebuilt, so the result shares no mutable object with the
        caller's tree and cannot be changed underneath a holder of the result.

        When ``blobs`` is given, string leaves over ``blob_threshold_bytes`` are also
        extracted into that store in the same pass (see ``_redact_leaf``).
        """
        # A length no string can reach stands in for "no blob store", so the walk asks
        # one question per leaf instead of two.
        blob_min = blob_threshold_bytes // 4 if blobs is not None else _NEVER_BLOB
        return self._redact_any(value, blobs, blob_threshold_bytes, blob_min)

    def _redact_any(
        self, value: Any, store: BlobStore | None, threshold: int, blob_min: int
    ) -> Any:
        if isinstance(value, dict):
            source = cast("dict[Any, Any]", value)
            new_dict: dict[Any, Any] = {}
            for k, v in source.items():
                if k in _SAFE_KEYS:  # a frozenset of str: non-str keys fall through
                    new_k: Any = k
                elif isinstance(k, str):
                    if k.startswith("[") and PLACEHOLDER_RE.fullmatch(k):
                        new_k = k
                    else:
                        new_k = self.redact_text(k) if len(k) >= 8 else k
                        if len(k) >= 3 and _SENSITIVE_KEY.search(k):
                            s_v = str(v)
                            if s_v.startswith("[") and PLACEHOLDER_RE.fullmatch(s_v):
                                new_dict[new_k] = v
                            else:
                                new_dict[new_k] = self.placeholder("key_name", s_v)
                            continue
                else:
                    # Keys are never blob-extracted: a ref would not survive inlining.
                    new_k = self._redact_any(k, None, threshold, _NEVER_BLOB)

                if isinstance(v, str):
                    # Role and status words dominate agent state; clearing them here
                    # saves the call into _redact_leaf, which would only repeat this
                    # same lookup. The length guard keeps a huge string from being
                    # hashed just to miss the set.
                    if len(v) < 10 and v in _SAFE_VALUES:
                        new_dict[new_k] = v
                    else:
                        new_dict[new_k] = self._redact_leaf(v, store, threshold, blob_min)
                else:
                    new_dict[new_k] = self._redact_any(v, store, threshold, blob_min)

            return new_dict

        if isinstance(value, list):
            items = cast("list[Any]", value)
            return [
                self._redact_leaf(item, store, threshold, blob_min)
                if isinstance(item, str)
                else self._redact_any(item, store, threshold, blob_min)
                for item in items
            ]

        if isinstance(value, str):
            return self._redact_leaf(value, store, threshold, blob_min)

        if isinstance(value, tuple):
            entries = cast("tuple[Any, ...]", value)
            return tuple(
                self._redact_leaf(item, store, threshold, blob_min)
                if isinstance(item, str)
                else self._redact_any(item, store, threshold, blob_min)
                for item in entries
            )

        if isinstance(value, bytearray):
            return bytearray(value)
        if isinstance(value, set):
            s_set = cast("set[Any]", value)
            return set(s_set)
        if isinstance(value, memoryview):
            return value.tobytes()

        return value

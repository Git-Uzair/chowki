# python/chowki/src/chowki/state/redact.py
from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final, cast

import structlog

logger = structlog.get_logger()

__all__ = ["PLACEHOLDER_RE", "Redactor"]

PLACEHOLDER_RE: Final = re.compile(r"\[REDACTED:[a-z0-9_]+:[0-9a-f]{8}\]")

_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "private_key",
        r"-----BEGIN[A-Z \-]*PRIVATE KEY-----[\s\S]*?-----END[A-Z \-]*PRIVATE KEY-----",
    ),
    ("jwt", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
    ("openai_proj", r"sk-proj-[A-Za-z0-9\-_]{40,}"),
    ("anthropic", r"sk-ant-[A-Za-z0-9\-_]{40,}"),
    ("openai", r"sk-[A-Za-z0-9\-_]{20,}"),
    ("stripe", r"(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"),
    ("aws_access", r"(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("aws_secret", r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{20,}"),
    ("github", r"ghp_[A-Za-z0-9]{36}\b"),
    ("slack", r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
    ("bearer", r"Bearer\s+[A-Za-z0-9\-._~+/]{10,}=*"),
    ("basic", r"Basic\s+[A-Za-z0-9+/]{10,}={0,2}"),
    ("uri_userinfo", r"(?<=://)[^\s'\"/]*:[^\s'\"@/]+(?=@)"),
)

_INDICATORS: Final = (
    "-----",
    "eyJ",
    "sk-",
    "sk_",
    "pk_",
    "AKIA",
    "ASIA",
    "aws_secret",
    "ghp_",
    "xox",
    "Bearer",
    "Basic",
    "://",
)

_DIGITS: Final = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")

_SENSITIVE_KEY: Final = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|auth(?:orization)?|credential|private[_-]?key|access[_-]?key)"
)

_UUID_RE: Final = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE: Final = re.compile(r"^[0-9a-fA-F]+$")
_HAS_DIGIT: Final = re.compile(r"\d")
_CANDIDATE: Final = re.compile(r"[A-Za-z0-9+/=_\-.!@#$%&*]{12,}")


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_safe(token: str) -> bool:
    if _UUID_RE.match(token) is not None:
        return True
    if len(token) in (16, 32, 40, 64) and _HEX_RE.match(token) is not None:
        return True
    if "/" in token or "\\" in token:
        return True
    if not _HAS_DIGIT.search(token):
        return True
    if _is_number(token):
        return True
    return token.startswith("REDACTED")


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
        entropy_max_scan_bytes: int = 65_536,
        extra_patterns: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._hmac_key = hmac_key
        self.entropy_threshold = entropy_threshold
        self.min_token_len = min_token_len
        self.enable_entropy = enable_entropy
        self.entropy_max_scan_bytes = entropy_max_scan_bytes
        self._has_extra_patterns = bool(extra_patterns)
        self._sensitive_cache: dict[str, bool] = {}
        self._text_cache: dict[str, str] = {}

        patterns: list[tuple[str, str]] = list(_PATTERNS)
        if extra_patterns:
            patterns.extend(extra_patterns)
        combined_pattern = "|".join(f"(?P<{name}>{pat})" for name, pat in patterns)
        self._combined_re = re.compile(combined_pattern)

    def placeholder(self, kind: str, secret: str) -> str:
        short = hmac.new(self._hmac_key, secret.encode("utf-8"), hashlib.sha256).hexdigest()[:8]
        kind_clean = re.sub(r"[^a-z0-9_]", "_", kind.lower()) or "secret"
        return f"[REDACTED:{kind_clean}:{short}]"

    def _sub_layer1(self, match: re.Match[str]) -> str:
        kind = match.lastgroup or "secret"
        secret = match.group()
        return self.placeholder(kind, secret)

    def _sub_layer2(self, match: re.Match[str]) -> str:
        token = match.group()
        if (
            len(token) >= self.min_token_len
            and _shannon(token) >= self.entropy_threshold
            and not _is_safe(token)
        ):
            return self.placeholder("entropy", token)
        return token

    def redact_text(self, text: str) -> str:
        if len(text) < 8:
            return text

        cached = self._text_cache.get(text)
        if cached is not None:
            return cached

        res = text

        if not self._has_extra_patterns and not (
            "-" in text
            or "_" in text
            or ":" in text
            or "/" in text
            or "=" in text
            or "0" in text
            or "1" in text
            or "2" in text
            or "3" in text
            or "4" in text
            or "5" in text
            or "6" in text
            or "7" in text
            or "8" in text
            or "9" in text
            or "Bearer" in text
            or "Basic" in text
            or "AKIA" in text
            or "ASIA" in text
            or "xox" in text
            or "eyJ" in text
        ):
            if len(self._text_cache) < 10_000:
                self._text_cache[text] = text
            return text

        has_ind = self._has_extra_patterns or any(ind in text for ind in _INDICATORS)
        has_digit = any(d in text for d in _DIGITS)

        # Short-circuit if no indicators for Layer 1 and no digits for Layer 2
        if not has_ind and not has_digit:
            if len(self._text_cache) < 10_000:
                self._text_cache[text] = text
            return text

        # Layer 1: Compiled combined regex pass
        if has_ind:
            res = self._combined_re.sub(self._sub_layer1, res)

        # Layer 2: Entropy scan
        if self.enable_entropy and has_digit:
            if len(res) > self.entropy_max_scan_bytes:
                logger.debug(
                    "redact_entropy_skipped_large_string",
                    length=len(res),
                    max_bytes=self.entropy_max_scan_bytes,
                )
            else:
                res = _CANDIDATE.sub(self._sub_layer2, res)

        if len(self._text_cache) < 10_000:
            self._text_cache[text] = res

        return res

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            if len(value) < 8:
                return value
            cached = self._text_cache.get(value)
            if cached is not None:
                return cached
            return self.redact_text(value)

        if isinstance(value, dict):
            text_cache = self._text_cache
            sensitive_cache = self._sensitive_cache
            dict_items: dict[Any, Any] = cast(Any, value)
            new_dict: dict[Any, Any] = {}
            for k, v in dict_items.items():
                if isinstance(k, str):
                    if len(k) < 8:
                        new_k = k
                    else:
                        cached_k = text_cache.get(k)
                        new_k = cached_k if cached_k is not None else self.redact_text(k)
                    is_sens = sensitive_cache.get(k)
                    if is_sens is None:
                        is_sens = bool(_SENSITIVE_KEY.search(k)) if len(k) >= 4 else False
                        if len(sensitive_cache) < 1_000:
                            sensitive_cache[k] = is_sens

                    if is_sens:
                        new_dict[new_k] = self.placeholder("key_name", str(v))
                        continue
                else:
                    new_k = self.redact(k)

                if isinstance(v, str):
                    if len(v) < 8:
                        new_dict[new_k] = v
                    else:
                        cached_v = text_cache.get(v)
                        new_dict[new_k] = cached_v if cached_v is not None else self.redact_text(v)
                elif isinstance(v, (dict, list, tuple)):
                    new_dict[new_k] = self.redact(v)
                else:
                    new_dict[new_k] = v
            return new_dict

        if isinstance(value, list):
            list_items: list[Any] = cast(Any, value)
            return [self.redact(item) for item in list_items]

        if isinstance(value, tuple):
            tuple_items: tuple[Any, ...] = cast(Any, value)
            return tuple(self.redact(item) for item in tuple_items)

        return value

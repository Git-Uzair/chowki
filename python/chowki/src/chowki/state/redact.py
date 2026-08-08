# python/chowki/src/chowki/state/redact.py
from __future__ import annotations

import hashlib
import hmac
import math
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final

import structlog

logger = structlog.get_logger()

__all__ = ["PLACEHOLDER_RE", "Redactor"]

PLACEHOLDER_RE: Final = re.compile(r"\[REDACTED:[a-z0-9_]+:[0-9a-f]{8}\]")

_HAS_DIGIT: Final = re.compile(r"\d")
_DIGITS_TUPLE: Final = ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9")

_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    (
        "private_key",
        r"-----BEGIN[A-Z \-]*PRIVATE KEY-----[\s\S]*?-----END[A-Z \-]*PRIVATE KEY-----",
    ),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
    ("openai_proj", r"\bsk-proj-[A-Za-z0-9\-_]{40,}"),
    ("anthropic", r"\bsk-ant-[A-Za-z0-9\-_]{40,}"),
    ("openai", r"(?<![a-zA-Z])sk-[A-Za-z0-9\-_]{20,}"),
    ("stripe", r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"),
    ("aws_access", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("aws_secret", r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{20,}"),
    ("github", r"\bghp_[A-Za-z0-9]{36}\b"),
    ("slack", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    ("bearer", r"\bBearer\s+[A-Za-z0-9\-._~+/]{10,}=*"),
    ("basic", r"\bBasic\s+[A-Za-z0-9+/]{10,}={0,2}"),
    ("uri_userinfo", r"(?<=://)[^\s'\"/]*:[^\s'\"@/]+(?=@)"),
)

_DEFAULT_COMBINED_RE: Final = re.compile("|".join(f"(?P<{name}>{pat})" for name, pat in _PATTERNS))

_HAS_INDICATOR: Final = re.compile(
    r"-----|eyJ|sk-|sk_|pk_|AKIA|ASIA|aws_secret|ghp_|xox|Bearer|Basic|://"
)

_SENSITIVE_KEY: Final = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|auth(?:orization)?|credential|private[_-]?key|access[_-]?key)"
)
_SAFE_KEYS: Final = frozenset(
    {"role", "content", "messages", "type", "name", "id", "text", "user", "system", "assistant"}
)
_SAFE_VALUES: Final = frozenset(
    {"user", "assistant", "system", "tool", "function", "pending", "running", "completed", "failed"}
)
_INDICATOR_TUPLE: Final = (
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

_UUID_RE: Final = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE: Final = re.compile(r"^[0-9a-fA-F]+$")
_CANDIDATE: Final = re.compile(r"[A-Za-z0-9+/=_\-.!@#$%&*]{12,}")


_CONTAINER_TYPES: Final = (dict, list, tuple)


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
        entropy_max_scan_bytes: int = 65_536,
        extra_patterns: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._hmac_key = hmac_key
        self.entropy_threshold = entropy_threshold
        self.min_token_len = min_token_len
        self.enable_entropy = enable_entropy
        self.entropy_max_scan_bytes = entropy_max_scan_bytes
        self._has_extra_patterns = bool(extra_patterns)
        self._safe_text_cache: dict[tuple[str, bool], str] = {}

        if extra_patterns:
            patterns: list[tuple[str, str]] = list(_PATTERNS)
            for i, (name, pat) in enumerate(extra_patterns):
                clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", name) or "extra"
                patterns.append((f"extra_{i}_{clean_name}", pat))
            combined_pattern = "|".join(f"(?P<{name}>{pat})" for name, pat in patterns)
            self._combined_re = re.compile(combined_pattern)
        else:
            self._combined_re = _DEFAULT_COMBINED_RE

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
        if len(token) < self.min_token_len or _HAS_DIGIT.search(token) is None:
            return token
        if _is_safe(token):
            return token
        if _shannon(token) >= self.entropy_threshold:
            return self.placeholder("entropy", token)
        return token

    def redact_text(self, text: str) -> str:
        if len(text) < 8:
            return text

        cache_key = (text, self.enable_entropy)
        cached = self._safe_text_cache.get(cache_key)
        if cached is not None:
            return cached

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

        has_ind = self._has_extra_patterns or (_HAS_INDICATOR.search(working_text) is not None)
        res = self._combined_re.sub(self._sub_layer1, working_text) if has_ind else working_text

        if self.enable_entropy:
            if len(res) > self.entropy_max_scan_bytes:
                logger.debug(
                    "redact_entropy_skipped_large_string",
                    length=len(res),
                    max_bytes=self.entropy_max_scan_bytes,
                )
            elif _HAS_DIGIT.search(res) is not None and _CANDIDATE.search(res) is not None:
                res = _CANDIDATE.sub(self._sub_layer2, res)

        if placeholders and nonce is not None:
            unmask_re = re.compile(rf"\x00PH_{nonce}_(\d+)\x00")
            res = unmask_re.sub(lambda m: placeholders[int(m.group(1))], res)

        # Cache ONLY if NO secrets were found in the text
        if res == text and len(self._safe_text_cache) < 10_000:
            self._safe_text_cache[cache_key] = text

        return res

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            new_dict: dict[Any, Any] = {}
            dict_items: Any = value  # pyright: ignore[reportUnknownVariableType]
            for k, v in dict_items.items():
                if isinstance(k, str) and k in _SAFE_KEYS:
                    new_k: Any = k
                elif isinstance(k, str):
                    new_k = self.redact_text(k) if len(k) >= 8 else k
                    if len(k) >= 3 and _SENSITIVE_KEY.search(k):
                        new_dict[new_k] = self.placeholder("key_name", str(v))
                        continue
                else:
                    new_k = self.redact(k)

                if isinstance(v, str):
                    new_dict[new_k] = (
                        v if (len(v) < 8 or v in _SAFE_VALUES) else self.redact_text(v)
                    )
                elif isinstance(v, _CONTAINER_TYPES):
                    new_dict[new_k] = self.redact(v)
                else:
                    new_dict[new_k] = v
            return new_dict

        if isinstance(value, list):
            list_items: Any = value  # pyright: ignore[reportUnknownVariableType]
            return [self.redact(item) for item in list_items]

        if isinstance(value, str):
            if len(value) < 8:
                return value
            return self.redact_text(value)

        if isinstance(value, tuple):
            tuple_items: Any = value  # pyright: ignore[reportUnknownVariableType]
            return tuple(self.redact(item) for item in tuple_items)

        return value

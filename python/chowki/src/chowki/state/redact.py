# python/chowki/src/chowki/state/redact.py
from __future__ import annotations

import hashlib
import hmac
import math
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final, cast

import structlog

logger = structlog.get_logger()

__all__ = ["PLACEHOLDER_RE", "Redactor"]

PLACEHOLDER_RE: Final = re.compile(r"\[REDACTED:[a-z0-9_]+:[0-9a-f]{8}\]")

_PROSE_RE: Final = re.compile(r"^[A-Za-z. ,!?'\"\n\r\t]+$")

_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "private_key",
        r"-----BEGIN[A-Z \-]*PRIVATE KEY-----[\s\S]*?-----END[A-Z \-]*PRIVATE KEY-----",
    ),
    ("jwt", r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
    ("openai_proj", r"(?<![A-Za-z0-9])sk-proj-[A-Za-z0-9\-_]{40,}"),
    ("anthropic", r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9\-_]{40,}"),
    ("openai", r"(?<![A-Za-z0-9])sk-[A-Za-z0-9\-_]{20,}"),
    ("stripe", r"(?<![A-Za-z0-9])(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}"),
    ("aws_access", r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("aws_secret", r"aws_secret_access_key\s*[:=]\s*[A-Za-z0-9/+=]{20,}"),
    ("github", r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{36}\b"),
    ("slack", r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9\-]{10,}"),
    ("bearer", r"(?<![A-Za-z0-9])Bearer\s+[A-Za-z0-9\-._~+/]{10,}=*"),
    ("basic", r"(?<![A-Za-z0-9])Basic\s+[A-Za-z0-9+/]{10,}={0,2}"),
    ("uri_userinfo", r"(?<=://)[^\s'\"/]*:[^\s'\"@/]+(?=@)"),
)

_HAS_INDICATOR: Final = re.compile(
    r"-----|eyJ|sk-|sk_|pk_|AKIA|ASIA|aws_secret|ghp_|xox|Bearer|Basic|://", re.IGNORECASE
)
_HAS_DIGIT: Final = re.compile(r"\d")

_SENSITIVE_KEY: Final = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|auth(?:orization)?|credential|private[_-]?key|access[_-]?key)"
)
_SAFE_KEYS: Final = frozenset(
    {"role", "content", "messages", "type", "name", "id", "text", "user", "system", "assistant"}
)

_UUID_RE: Final = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE: Final = re.compile(r"^[0-9a-fA-F]+$")
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
        self._safe_text_cache: dict[str, str] = {}

        patterns: list[tuple[str, str]] = list(_PATTERNS)
        if extra_patterns:
            for i, (name, pat) in enumerate(extra_patterns):
                clean_name = re.sub(r"[^a-zA-Z0-9_]", "_", name) or "extra"
                patterns.append((f"extra_{i}_{clean_name}", pat))
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

        cached = self._safe_text_cache.get(text)
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
        has_digit = _HAS_DIGIT.search(working_text) is not None

        if not has_ind and not has_digit:
            res = working_text
        else:
            res = working_text
            if has_ind:
                res = self._combined_re.sub(self._sub_layer1, res)

            if self.enable_entropy and has_digit:
                if len(res) > self.entropy_max_scan_bytes:
                    logger.debug(
                        "redact_entropy_skipped_large_string",
                        length=len(res),
                        max_bytes=self.entropy_max_scan_bytes,
                    )
                else:
                    res = _CANDIDATE.sub(self._sub_layer2, res)

        if placeholders and nonce is not None:
            unmask_re = re.compile(rf"\x00PH_{nonce}_(\d+)\x00")
            res = unmask_re.sub(lambda m: placeholders[int(m.group(1))], res)

        # Only cache strings that contain NO redacted secrets
        if res == text and len(self._safe_text_cache) < 10_000:
            self._safe_text_cache[text] = res

        return res

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            if len(value) < 8:
                return value
            cached = self._safe_text_cache.get(value)
            return cached if cached is not None else self.redact_text(value)

        if isinstance(value, dict):
            dict_items = cast(Any, value)
            new_dict: dict[Any, Any] = {}
            safe_cache = self._safe_text_cache
            for k, v in dict_items.items():
                if isinstance(k, str):
                    if k in _SAFE_KEYS:
                        new_k = k
                    else:
                        cached_k = safe_cache.get(k)
                        new_k = cached_k if cached_k is not None else self.redact_text(k)
                        if _SENSITIVE_KEY.search(k) if len(k) >= 3 else False:
                            if isinstance(v, str) and PLACEHOLDER_RE.fullmatch(v):
                                new_dict[new_k] = v
                            else:
                                new_dict[new_k] = self.placeholder("key_name", str(v))
                            continue
                else:
                    new_k = self.redact(k)

                if isinstance(v, str):
                    if len(v) < 8:
                        new_dict[new_k] = v
                    else:
                        cached_v = safe_cache.get(v)
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

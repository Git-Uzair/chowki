# python/chowki/src/chowki/hitl/tokens.py
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

import msgspec

from chowki.errors import ExpiredResumeToken, InvalidResumeToken, ReplayedNonceError

if TYPE_CHECKING:
    from chowki.storage.base import StorageAdapter


class ResumeClaims(msgspec.Struct, kw_only=True, frozen=True):
    run_id: str
    step_id: str
    permitted_actions: tuple[str, ...]
    nonce: str
    iat: int
    exp: int
    allowed_roles: tuple[str, ...] = ()


def _b64encode_unpadded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode_unpadded(s: str) -> bytes:
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def decode_unverified(token: str) -> ResumeClaims:
    """Decode token claims without signature or nonce checks.

    never make an authorisation decision on this.
    """
    if "." not in token:
        raise InvalidResumeToken("malformed token")
    body, _ = token.rsplit(".", 1)
    try:
        body_bytes = _b64decode_unpadded(body)
        return msgspec.msgpack.decode(body_bytes, type=ResumeClaims)
    except Exception as exc:
        raise InvalidResumeToken(f"failed to decode token claims: {exc}") from exc


class TokenIssuer:
    def __init__(
        self,
        *,
        secret: bytes,
        storage: StorageAdapter,
        default_ttl: int = 86400,
    ) -> None:
        self._secret = secret
        self._storage = storage
        self._default_ttl = default_ttl

    def issue(
        self,
        *,
        run_id: str,
        step_id: str,
        permitted_actions: Sequence[str] | tuple[str, ...],
        ttl: int | None = None,
        allowed_roles: Sequence[str] = (),
    ) -> str:
        nonce = uuid.uuid4().hex
        iat = int(time.time())
        exp = iat + (ttl if ttl is not None else self._default_ttl)
        claims = ResumeClaims(
            run_id=run_id,
            step_id=step_id,
            permitted_actions=tuple(permitted_actions),
            nonce=nonce,
            iat=iat,
            exp=exp,
            allowed_roles=tuple(allowed_roles),
        )
        body_bytes = msgspec.msgpack.encode(claims)
        body = _b64encode_unpadded(body_bytes)
        sig_bytes = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).digest()
        sig = _b64encode_unpadded(sig_bytes)
        return f"{body}.{sig}"

    def verify(self, token: str, *, action: str) -> ResumeClaims:
        if "." not in token:
            raise InvalidResumeToken("malformed token")
        body, sig = token.rsplit(".", 1)

        expected_sig_bytes = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).digest()
        expected_sig = _b64encode_unpadded(expected_sig_bytes)

        try:
            is_valid = hmac.compare_digest(sig, expected_sig)
        except (TypeError, ValueError):
            is_valid = False

        if not is_valid:
            raise InvalidResumeToken("signature mismatch")

        try:
            body_bytes = _b64decode_unpadded(body)
            claims = msgspec.msgpack.decode(body_bytes, type=ResumeClaims)
        except Exception as exc:
            raise InvalidResumeToken(f"failed to decode claims: {exc}") from exc

        if claims.exp <= time.time():
            raise ExpiredResumeToken("token expired")

        if action not in claims.permitted_actions:
            raise InvalidResumeToken(f"action {action!r} is not permitted")

        consumed = self._storage.consume_nonce(claims.nonce, expires_at_epoch=claims.exp)
        if not consumed:
            raise ReplayedNonceError("this chowki action was already processed")

        return claims

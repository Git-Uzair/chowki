"""AES-256-GCM encryption at rest and KeyRing management.

ChaCha20-Poly1305 fallback for AES-NI-less hardware is # TODO(phase-5)
(02-serialization.md:231, docs/plans/00-roadmap.md); the KeyRing interface already
carries the key id needed to select an algorithm per key.
"""

from __future__ import annotations

import base64
import os
from typing import Final

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from chowki.errors import ChowkiConfigError, DecryptionError

logger = structlog.get_logger(__name__)

NONCE_BYTES: Final = 12
KEY_BYTES: Final = 32
ENV_VAR: Final = "CHOWKI_MASTER_KEY"


class KeyRing:
    """KeyRing manages encryption keys and cipher instances for AEAD operations."""

    def __init__(self, keys: dict[str, bytes], active_key_id: str) -> None:
        if not keys:
            raise ChowkiConfigError("keys mapping cannot be empty")
        if active_key_id not in keys:
            raise ChowkiConfigError(f"active_key_id {active_key_id!r} not found in keys")

        self._ciphers: dict[str, AESGCM] = {}
        for kid, key in keys.items():
            if len(key) != KEY_BYTES:
                raise ChowkiConfigError(f"chowki keys must be {KEY_BYTES} bytes")
            self._ciphers[kid] = AESGCM(key)

        self._active_key_id: str = active_key_id

    @classmethod
    def from_key(cls, key: bytes, *, key_id: str = "k1") -> KeyRing:
        return cls({key_id: key}, active_key_id=key_id)

    @classmethod
    def from_env(cls) -> KeyRing:
        raw = os.environ.get(ENV_VAR)
        if not raw:
            raise ChowkiConfigError(f"{ENV_VAR} environment variable is not set")
        try:
            key = base64.b64decode(raw, validate=True)
        except Exception as err:
            raise ChowkiConfigError(f"invalid base64 key in {ENV_VAR}") from err

        if len(key) != KEY_BYTES:
            raise ChowkiConfigError(f"chowki keys must be {KEY_BYTES} bytes")

        return cls.from_key(key, key_id="k1")

    @classmethod
    def generate(cls) -> KeyRing:
        logger.warning("generated ephemeral keyring for development/testing")
        key = AESGCM.generate_key(bit_length=256)
        return cls.from_key(key, key_id="k1")

    def rotate(self, key: bytes, *, key_id: str) -> None:
        if len(key) != KEY_BYTES:
            raise ChowkiConfigError(f"chowki keys must be {KEY_BYTES} bytes")
        self._ciphers[key_id] = AESGCM(key)
        self._active_key_id = key_id

    def cipher(self, key_id: str) -> AESGCM:
        if key_id not in self._ciphers:
            raise DecryptionError(f"unknown key id {key_id!r}")
        return self._ciphers[key_id]

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def __repr__(self) -> str:
        return f"KeyRing(active={self.active_key_id!r}, keys={len(self._ciphers)})"

    __str__ = __repr__


def encrypt(plaintext: bytes, ring: KeyRing, *, aad: bytes) -> tuple[bytes, str, bytes]:
    """Encrypt plaintext using the active key from the KeyRing with AES-256-GCM."""
    key_id = ring.active_key_id
    cipher = ring.cipher(key_id)
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = cipher.encrypt(nonce, plaintext, aad)
    return ciphertext, key_id, nonce


def decrypt(blob: bytes, ring: KeyRing, *, key_id: str, nonce: bytes, aad: bytes) -> bytes:
    """Decrypt ciphertext using the specified key id from the KeyRing."""
    cipher = ring.cipher(key_id)
    try:
        return cipher.decrypt(nonce, blob, aad)
    except InvalidTag:
        raise DecryptionError(
            "chowki snapshot failed authentication: wrong key, wrong AAD, or tampered ciphertext"
        ) from None

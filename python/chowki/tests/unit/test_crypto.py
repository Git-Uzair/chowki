from __future__ import annotations

import base64
import os

import pytest

from chowki.errors import ChowkiConfigError, DecryptionError
from chowki.state.crypto import NONCE_BYTES, KeyRing, decrypt, encrypt


@pytest.fixture
def ring() -> KeyRing:
    return KeyRing.from_key(b"k" * 32, key_id="k1")


def test_roundtrip(ring: KeyRing) -> None:
    aad = b"tenant_1:run_1:v1"
    blob, key_id, nonce = encrypt(b"secret state", ring, aad=aad)
    assert key_id == "k1"
    assert len(nonce) == NONCE_BYTES == 12
    assert decrypt(blob, ring, key_id=key_id, nonce=nonce, aad=aad) == b"secret state"


def test_ciphertext_is_not_plaintext(ring: KeyRing) -> None:
    blob, _, _ = encrypt(b"secret state", ring, aad=b"a")
    assert b"secret state" not in blob


def test_nonce_is_unique_per_call(ring: KeyRing) -> None:
    nonces = {encrypt(b"x", ring, aad=b"a")[2] for _ in range(200)}
    assert len(nonces) == 200


def test_wrong_aad_fails_closed(ring: KeyRing) -> None:
    """Cross-tenant ciphertext transplantation must be impossible (ADR-003)."""
    blob, key_id, nonce = encrypt(b"state", ring, aad=b"tenant_1:run_1:v1")
    with pytest.raises(DecryptionError):
        decrypt(blob, ring, key_id=key_id, nonce=nonce, aad=b"tenant_2:run_1:v1")


def test_tampered_ciphertext_fails_closed(ring: KeyRing) -> None:
    blob, key_id, nonce = encrypt(b"state", ring, aad=b"a")
    flipped = bytes([blob[0] ^ 0xFF]) + blob[1:]
    with pytest.raises(DecryptionError):
        decrypt(flipped, ring, key_id=key_id, nonce=nonce, aad=b"a")


def test_rotation_keeps_old_snapshots_readable() -> None:
    ring = KeyRing.from_key(b"a" * 32, key_id="k1")
    old_blob, old_id, old_nonce = encrypt(b"old", ring, aad=b"a")

    ring.rotate(b"b" * 32, key_id="k2")
    new_blob, new_id, new_nonce = encrypt(b"new", ring, aad=b"a")

    assert new_id == "k2"
    assert decrypt(new_blob, ring, key_id="k2", nonce=new_nonce, aad=b"a") == b"new"
    assert decrypt(old_blob, ring, key_id=old_id, nonce=old_nonce, aad=b"a") == b"old"


def test_unknown_key_id_raises(ring: KeyRing) -> None:
    with pytest.raises(DecryptionError, match="unknown key"):
        decrypt(b"\x00" * 32, ring, key_id="nope", nonce=os.urandom(12), aad=b"a")


def test_from_env_reads_base64_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOWKI_MASTER_KEY", base64.b64encode(b"z" * 32).decode())
    ring = KeyRing.from_env()
    assert ring.active_key_id


def test_from_env_unset_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOWKI_MASTER_KEY", raising=False)
    with pytest.raises(
        ChowkiConfigError, match="CHOWKI_MASTER_KEY environment variable is not set"
    ):
        KeyRing.from_env()


def test_from_env_rejects_a_short_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOWKI_MASTER_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(ChowkiConfigError, match="32 bytes"):
        KeyRing.from_env()


def test_keyring_invalid_key_length() -> None:
    with pytest.raises(ChowkiConfigError, match="32 bytes"):
        KeyRing({"k1": b"too short"}, active_key_id="k1")


def test_keyring_active_key_not_in_keys() -> None:
    with pytest.raises(ChowkiConfigError, match="active_key_id"):
        KeyRing({"k1": b"k" * 32}, active_key_id="k2")


def test_keyring_generate() -> None:
    ring = KeyRing.generate()
    assert ring.active_key_id == "k1"
    blob, key_id, nonce = encrypt(b"hello", ring, aad=b"aad")
    assert decrypt(blob, ring, key_id=key_id, nonce=nonce, aad=b"aad") == b"hello"


def test_keyring_never_prints_key_material(ring: KeyRing) -> None:
    assert "kkkk" not in repr(ring)
    assert "kkkk" not in str(ring)
    assert "KeyRing" in repr(ring)
    assert repr(ring) == str(ring)

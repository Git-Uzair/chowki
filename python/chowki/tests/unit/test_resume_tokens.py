# python/chowki/tests/unit/test_resume_tokens.py
from __future__ import annotations

import time

import pytest

from chowki.errors import (
    ExpiredResumeToken,
    InvalidResumeToken,
    ReplayedNonceError,
)
from chowki.hitl.tokens import TokenIssuer, decode_unverified
from chowki.storage.memory import MemoryStorage

SECRET = b"a-32-byte-or-longer-test-secret!!"


@pytest.fixture
def issuer() -> TokenIssuer:
    return TokenIssuer(secret=SECRET, storage=MemoryStorage())


def test_issue_and_verify(issuer: TokenIssuer) -> None:
    token = issuer.issue(run_id="r1", step_id="approve#0", permitted_actions=("APPROVE", "REJECT"))
    claims = issuer.verify(token, action="APPROVE")
    assert claims.run_id == "r1"
    assert claims.step_id == "approve#0"
    assert claims.nonce


def test_token_is_url_safe_and_compact(issuer: TokenIssuer) -> None:
    """Slack button `value` is capped at 2000 chars (05-hitl-gateway.md:44)."""
    token = issuer.issue(run_id="r1", step_id="approve#0", permitted_actions=("APPROVE",))
    assert len(token) < 512
    assert token.replace("-", "").replace("_", "").replace(".", "").isalnum()


def test_tampering_with_the_payload_is_rejected(issuer: TokenIssuer) -> None:
    token = issuer.issue(run_id="r1", step_id="s#0", permitted_actions=("APPROVE",))
    body, sig = token.rsplit(".", 1)
    forged = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + sig
    with pytest.raises(InvalidResumeToken):
        issuer.verify(forged, action="APPROVE")


def test_a_token_from_another_secret_is_rejected() -> None:
    a = TokenIssuer(secret=SECRET, storage=MemoryStorage())
    b = TokenIssuer(secret=b"different-secret-of-sufficient-len", storage=MemoryStorage())
    token = a.issue(run_id="r", step_id="s", permitted_actions=("APPROVE",))
    with pytest.raises(InvalidResumeToken):
        b.verify(token, action="APPROVE")


def test_expired_token_is_rejected(issuer: TokenIssuer) -> None:
    token = issuer.issue(run_id="r", step_id="s", permitted_actions=("APPROVE",), ttl=-1)
    with pytest.raises(ExpiredResumeToken):
        issuer.verify(token, action="APPROVE")


def test_default_ttl_is_24_hours(issuer: TokenIssuer) -> None:
    token = issuer.issue(run_id="r", step_id="s", permitted_actions=("APPROVE",))
    claims = decode_unverified(token)
    assert 86_000 < claims.exp - int(time.time()) <= 86_400


def test_scope_is_enforced(issuer: TokenIssuer) -> None:
    token = issuer.issue(run_id="r", step_id="s", permitted_actions=("APPROVE",))
    with pytest.raises(InvalidResumeToken, match="not permitted"):
        issuer.verify(token, action="REJECT")


def test_nonce_is_single_use(issuer: TokenIssuer) -> None:
    token = issuer.issue(run_id="r", step_id="s", permitted_actions=("APPROVE",))
    issuer.verify(token, action="APPROVE")
    with pytest.raises(ReplayedNonceError):
        issuer.verify(token, action="APPROVE")


def test_nonces_are_unique_across_issues(issuer: TokenIssuer) -> None:
    nonces = {
        decode_unverified(
            issuer.issue(run_id="r", step_id="s", permitted_actions=("APPROVE",))
        ).nonce
        for _ in range(200)
    }
    assert len(nonces) == 200


def test_verification_is_constant_time() -> None:
    """hmac.compare_digest, not ==. Assert the call, since timing cannot be unit-tested."""
    import inspect

    from chowki.hitl import tokens

    source = inspect.getsource(tokens)
    assert "compare_digest" in source
    assert "== expected_sig" not in source

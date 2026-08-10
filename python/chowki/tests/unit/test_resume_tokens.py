# python/chowki/tests/unit/test_resume_tokens.py
from __future__ import annotations

import subprocess
import sys
import time
import warnings

import pytest

from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.errors import (
    ExpiredResumeToken,
    InvalidResumeToken,
    ReplayedNonceError,
)
from chowki.hitl.tokens import TokenIssuer, decode_unverified
from chowki.storage.memory import MemoryStorage

SECRET = b"a-32-byte-or-longer-test-secret!!"

#: Builds an engine with an ephemeral resume secret in a fresh interpreter. The ephemeral
#: secret warning must never reach stdout: other probes in this suite parse a subprocess's
#: stdout verbatim (``test_step_decorator.py``), so a log line there corrupts their data.
_ENGINE_PROBE = """
from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.storage.memory import MemoryStorage

ChowkiEngine(ChowkiConfig(storage=MemoryStorage())).close()
"""


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


def test_non_ascii_signature_segment_raises_invalid_token(issuer: TokenIssuer) -> None:
    token = issuer.issue(run_id="r1", step_id="s#0", permitted_actions=("APPROVE",))
    body, _ = token.rsplit(".", 1)
    forged = body + ".bädsigñature"
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


def test_an_ephemeral_resume_secret_is_warned_about() -> None:
    """Tokens minted with a per-process secret die on restart; the operator must be told."""
    with pytest.warns(UserWarning, match="ephemeral"):
        ChowkiEngine(ChowkiConfig(storage=MemoryStorage())).close()


@pytest.mark.parametrize("empty", [b"", ""])
def test_an_empty_resume_secret_is_treated_as_absent(empty: bytes | str) -> None:
    """An empty secret is not a secret: HMAC with b"" is forgeable by anyone."""
    with pytest.warns(UserWarning, match="ephemeral"):
        ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), resume_secret=empty)).close()


@pytest.mark.parametrize("empty", [b"", ""])
def test_a_token_forged_with_an_empty_key_is_rejected(empty: bytes | str) -> None:
    """The empty-key issuer stands in for an attacker who knows the config was blank."""
    with pytest.warns(UserWarning, match="ephemeral"):
        engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), resume_secret=empty))
    try:
        forged = TokenIssuer(secret=b"", storage=MemoryStorage()).issue(
            run_id="r", step_id="s", permitted_actions=("APPROVE",)
        )
        with pytest.raises(InvalidResumeToken):
            engine.tokens.verify(forged, action="APPROVE")
    finally:
        engine.close()


def test_a_configured_resume_secret_warns_about_nothing() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), resume_secret=SECRET)).close()


def test_the_warning_goes_to_stderr_leaving_stdout_clean() -> None:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _ENGINE_PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == ""
    assert "ephemeral" in proc.stderr


def test_verification_is_constant_time() -> None:
    """hmac.compare_digest, not ==. Assert the call, since timing cannot be unit-tested."""
    import inspect

    from chowki.hitl import tokens

    source = inspect.getsource(tokens)
    assert "compare_digest" in source
    assert "== expected_sig" not in source

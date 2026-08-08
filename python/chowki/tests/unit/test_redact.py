# python/chowki/tests/unit/test_redact.py
from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from chowki.state.redact import PLACEHOLDER_RE, Redactor

KEY = b"unit-test-hmac-key"

SECRETS = {
    "openai": "sk-" + "A1b2C3d4E5f6G7h8I9j0",
    "openai_project": "sk-proj-" + "x" * 45,
    "anthropic": "sk-ant-" + "y" * 45,
    "aws_access": "AKIAIOSFODNN7EXAMPLE",
    "github": "ghp_" + "z" * 36,
    "slack": "xoxb-1234567890-abcdefghijkl",
    "stripe": "sk_live_" + "q" * 24,
    "bearer": "Bearer abcdefghijklmnop1234==",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g",
    "pg_uri": "postgres://admin:hunter2supersecret@db.internal:5432/prod",
}


@pytest.fixture
def redactor() -> Redactor:
    return Redactor(hmac_key=KEY)


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_every_known_credential_format_is_redacted(redactor: Redactor, name: str) -> None:
    secret = SECRETS[name]
    out = redactor.redact_text(f"the value is {secret} ok")
    assert secret not in out
    assert PLACEHOLDER_RE.search(out) is not None


def test_uri_redaction_keeps_the_host(redactor: Redactor) -> None:
    out = redactor.redact_text(SECRETS["pg_uri"])
    assert "hunter2supersecret" not in out
    assert "db.internal:5432/prod" in out


def test_sensitive_key_names_redact_the_whole_value(redactor: Redactor) -> None:
    out = redactor.redact({"api_key": "totally-plain-value", "Authorization": "abc"})
    assert out["api_key"] != "totally-plain-value"
    assert PLACEHOLDER_RE.fullmatch(str(out["api_key"]))
    assert PLACEHOLDER_RE.fullmatch(str(out["Authorization"]))


def test_high_entropy_unknown_token_is_redacted(redactor: Redactor) -> None:
    unknown = "Zq7!vK2#pL9$xR4%tM6&wB8"
    out = redactor.redact_text(f"token={unknown}")
    assert unknown not in out


def test_high_entropy_symbol_token_without_digits_is_redacted(redactor: Redactor) -> None:
    unknown = "aB+cD=eF#gH$jK%mL&nP*qR@sT"
    out = redactor.redact_text(f"token={unknown}")
    assert unknown not in out
    assert PLACEHOLDER_RE.search(out) is not None


@pytest.mark.parametrize(
    "safe",
    [
        "550e8400-e29b-41d4-a716-446655440000",  # UUID
        "a" * 64,  # sha256 hex
        "e3b0c44298fc1c149afbf4c8996fb924",  # md5-ish hex
        "/usr/local/lib/python3.11/site-packages",  # path
        "https://example.com/docs/getting-started",  # url path
        "The quick brown fox jumps over the lazy dog",  # prose
    ],
)
def test_safe_patterns_are_not_redacted(redactor: Redactor, safe: str) -> None:
    assert redactor.redact_text(safe) == safe


def test_placeholder_is_deterministic_and_blinded(redactor: Redactor) -> None:
    a = redactor.redact_text(SECRETS["openai"])
    b = redactor.redact_text(SECRETS["openai"])
    c = redactor.redact_text(SECRETS["github"])
    assert a == b  # same secret -> same placeholder: diffs stay readable
    assert a != c  # different secrets never collide
    assert "A1b2C3d4" not in a


def test_redaction_is_recursive_and_non_mutating(redactor: Redactor) -> None:
    original = {"outer": {"list": [{"k": SECRETS["openai"]}]}, "n": 1}
    snapshot = {"outer": {"list": [{"k": SECRETS["openai"]}]}, "n": 1}
    out = redactor.redact(original)
    assert original == snapshot  # input untouched
    assert SECRETS["openai"] not in str(out)
    assert out["n"] == 1


def test_dict_keys_are_also_scanned(redactor: Redactor) -> None:
    out = redactor.redact({SECRETS["openai"]: "value"})
    assert SECRETS["openai"] not in str(out)


@given(st.text(max_size=200))
def test_redaction_never_raises_and_never_leaks(payload: str) -> None:
    assume(not payload or not payload[-1].isalpha())
    r = Redactor(hmac_key=KEY)
    hostile = f"{payload} {SECRETS['openai']} {payload}"
    out = r.redact_text(hostile)
    assert SECRETS["openai"] not in out


def test_hostile_placeholder_injection(redactor: Redactor) -> None:
    text = "[REDACTED:aa:0123abcd] \x00PH_9\x00 tail"
    out = redactor.redact_text(text)
    assert out == text


def test_redaction_cannot_be_disabled() -> None:
    """ADR-003: redaction is mandatory. Only the entropy tier is tunable."""
    r = Redactor(hmac_key=KEY, enable_entropy=False)
    assert SECRETS["aws_access"] not in r.redact_text(SECRETS["aws_access"])


def test_false_positive_words_not_redacted(redactor: Redactor) -> None:
    assert redactor.redact_text("task-management-system-2024") == "task-management-system-2024"
    assert redactor.redact_text("MyBearer") == "MyBearer"


def test_digit_preceding_secret_is_redacted(redactor: Redactor) -> None:
    secret = "0sk-A1b2C3d4E5f6G7h8I9j00"
    out = redactor.redact_text(secret)
    assert "sk-A1b2C3d4E5f6G7h8I9j00" not in out

    aws = "1AKIAIOSFODNN7EXAMPLE"
    out_aws = redactor.redact_text(aws)
    assert "AKIAIOSFODNN7EXAMPLE" not in out_aws


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_redaction_idempotency(redactor: Redactor, name: str) -> None:
    text = f"prefix {SECRETS[name]} suffix"
    once = redactor.redact_text(text)
    twice = redactor.redact_text(once)
    assert twice == once


def test_extra_patterns_duplicate_group_name() -> None:
    extra = [
        ("openai", r"sk-custom-[0-9a-f]{10}"),
        ("openai", r"sk-another-[0-9a-f]{10}"),
    ]
    r = Redactor(hmac_key=KEY, extra_patterns=extra)
    res = r.redact_text("here is sk-custom-0123456789")
    assert "sk-custom-0123456789" not in res
    assert PLACEHOLDER_RE.search(res) is not None

# python/chowki/tests/unit/test_redact.py
from __future__ import annotations

import pytest
from hypothesis import given
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


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_no_known_secret_is_screened_out_as_inert(redactor: Redactor, name: str) -> None:
    """Pins `_redact_leaf`'s inline screen to `_PATTERNS`: the container walk's fast path must not
    declare a string carrying a known credential inert and skip `redact_text`."""
    secret = SECRETS[name]
    out = redactor.redact({"messages": [{"content": f"the value is {secret} ok"}]})
    assert secret not in str(out)


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


def test_high_entropy_token_without_digits_skips_entropy_redaction(redactor: Redactor) -> None:
    unknown = "aB+cD=eF#gH$jK%mL&nP*qR@sT"
    out = redactor.redact_text(f"token={unknown}")
    assert out == f"token={unknown}"


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
    r = Redactor(hmac_key=KEY)
    hostile = payload + SECRETS["openai"] + payload
    out = r.redact_text(hostile)
    assert SECRETS["openai"] not in out


def test_embedded_letter_prefixed_secret_is_redacted(redactor: Redactor) -> None:
    """A secret pasted flush against a word must still be caught (verifier audit)."""
    out = redactor.redact_text("Ask-A1b2C3d4E5f6G7h8I9j0A")
    assert "sk-A1b2C3d4E5f6G7h8I9j0" not in out
    assert PLACEHOLDER_RE.search(out) is not None


def test_hostile_placeholder_injection(redactor: Redactor) -> None:
    text = "[REDACTED:aa:0123abcd] \x00PH_9\x00 tail"
    out = redactor.redact_text(text)
    assert out == text


def test_redaction_cannot_be_disabled() -> None:
    """ADR-003: redaction is mandatory. Only the entropy tier is tunable."""
    r = Redactor(hmac_key=KEY, enable_entropy=False)
    assert SECRETS["aws_access"] not in r.redact_text(SECRETS["aws_access"])


def test_false_positive_words_not_redacted(redactor: Redactor) -> None:
    assert redactor.redact_text("task-management-system-config") == "task-management-system-config"
    assert redactor.redact_text("MyBearer") == "MyBearer"


@pytest.mark.parametrize(
    "token",
    [
        "Bearer abcdefghijklmnop1234",
        "Bearer abcdef.ghijkl.mnopqrs",
        "Bearer abc-def_ghi9jklmnop",
        "Bearer abcdefghijklmnop1234==",
        "Basic abcdefghijklmnop1234==",
    ],
)
def test_bearer_and_basic_tokens_are_redacted(redactor: Redactor, token: str) -> None:
    out = redactor.redact_text(f"Authorization: {token}")
    assert token not in out
    assert PLACEHOLDER_RE.search(out) is not None


def test_bearer_and_basic_prose_words_not_redacted(redactor: Redactor) -> None:
    """Verifier audit: prose following "Bearer"/"Basic" must never be redacted."""
    for prose in (
        "Use Bearer token for the API",
        "Configure Basic auth settings today",
        "Bearer authentication is required",
        "Basic authentication is required",
        "The Bearer scheme transmits credentials in headers",
    ):
        assert redactor.redact_text(prose) == prose


def test_prose_with_sk_words_in_sentences_not_redacted(redactor: Redactor) -> None:
    text1 = "Please ask-for-the-longer-token when logging in."
    assert redactor.redact_text(text1) == text1

    text2 = "Update the task-management-system-config file."
    assert redactor.redact_text(text2) == text2

    text3 = "Check the disk-space-warning-threshold value."
    assert redactor.redact_text(text3) == text3

    text4 = "Review the risk-assessment-protocol-version now."
    assert redactor.redact_text(text4) == text4

    text5 = "Update the Task-Management-System-Config file."
    assert redactor.redact_text(text5) == text5

    text6 = "Review the risk-assessment-protocol-2024-version now."
    assert redactor.redact_text(text6) == text6

    text7 = "See disk-space-warning-threshold-v2 for details."
    assert redactor.redact_text(text7) == text7

    dict_out = redactor.redact({"Task-Management-System-Config": "value"})
    assert "Task-Management-System-Config" in dict_out


def test_short_uri_userinfo_and_credentials_redacted(redactor: Redactor) -> None:
    uri = "db://u:pw@h"  # 11 chars
    out = redactor.redact_text(uri)
    assert "u:pw" not in out
    assert PLACEHOLDER_RE.search(out) is not None


def test_punctuation_preceding_secret_is_redacted(redactor: Redactor) -> None:
    secret = ":sk-A1b2C3d4E5f6G7h8I9j00"
    out = redactor.redact_text(secret)
    assert "sk-A1b2C3d4E5f6G7h8I9j00" not in out

    aws = ":AKIAIOSFODNN7EXAMPLE"
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


def test_extra_patterns_without_builtin_indicators() -> None:
    r = Redactor(hmac_key=KEY, extra_patterns=[("corp", r"CORP[0-9]{10}")])
    out = r.redact_text("id CORP1234567890 end")
    assert "CORP1234567890" not in out
    assert PLACEHOLDER_RE.search(out) is not None


def test_extra_patterns_match_all_letter_tokens_inside_containers() -> None:
    """A user pattern that matches an all-letter token must fire through redact() too.

    The all-alpha fast path in the container walk is only sound for the built-in
    patterns; a caller-supplied pattern can match letters only.
    """
    r = Redactor(hmac_key=KEY, extra_patterns=[("xy", r"XYZ[A-Z]{6}")])
    out = r.redact({"note": "XYZACDEFG"})
    assert "XYZACDEFG" not in str(out)
    assert PLACEHOLDER_RE.fullmatch(str(out["note"])) is not None
    assert PLACEHOLDER_RE.fullmatch(str(r.redact("XYZACDEFG"))) is not None
    assert PLACEHOLDER_RE.fullmatch(str(r.redact(["XYZACDEFG"])[0])) is not None


def test_short_string_uri_userinfo_redaction(redactor: Redactor) -> None:
    out = redactor.redact_text("http://u:p1@x")
    assert "u:p1" not in out
    assert PLACEHOLDER_RE.search(out) is not None


def test_redact_handles_lone_surrogates(redactor: Redactor) -> None:
    dict_out = redactor.redact({"api_key": "\ud800"})
    assert isinstance(dict_out["api_key"], str)
    text_out = redactor.redact_text("http://\ud800aa:pw1234@host")
    assert isinstance(text_out, str)


def test_re_redact_is_idempotent(redactor: Redactor) -> None:
    a = redactor.redact({"api_key": "my-secret-key"})
    b = redactor.redact(a)
    assert a == b


def test_entropy_params_apply_immediately() -> None:
    r = Redactor(hmac_key=KEY, entropy_threshold=4.5)
    text = "abcdef123456gh"
    res1 = r.redact_text(text)
    assert res1 == text
    r.entropy_threshold = 3.0
    res2 = r.redact_text(text)
    assert res2 != text
    assert PLACEHOLDER_RE.search(res2) is not None


def test_oversized_strings_skip_entropy_but_not_layer_one() -> None:
    """Strings above entropy_max_scan_bytes skip layer 2 only; layer 1 always runs."""
    r = Redactor(hmac_key=KEY, entropy_max_scan_bytes=64)
    big = ("lorem ipsum dolor sit amet " * 4) + SECRETS["openai"]
    assert len(big) > 64
    out = r.redact_text(big)
    assert SECRETS["openai"] not in out
    assert r.entropy_skip_count == 1

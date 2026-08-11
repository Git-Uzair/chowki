# python/chowki/tests/unit/test_audit.py
from __future__ import annotations

from typing import Any, cast

from chowki.hitl.audit import AuditLog, build_audit_record, redact_patch
from chowki.storage.memory import MemoryStorage


def test_record_shape_matches_the_governance_spec() -> None:
    rec = build_audit_record(
        run_id="r1",
        step_id="s#0",
        action="EDIT",
        actor={"platform": "slack", "user_id": "U1"},
        original_state_hash="sha256:" + "a" * 64,
        patched_state_hash="sha256:" + "b" * 64,
        json_patch=[{"op": "replace", "path": "/a", "value": 1}],
        nonce="n1",
    )
    assert set(rec.keys()) == {
        "audit_id",
        "timestamp",
        "run_id",
        "step_id",
        "actor",
        "action",
        "original_state_hash",
        "patched_state_hash",
        "json_patch",
        "verification_details",
    }
    details = rec["verification_details"]
    assert isinstance(details, dict)
    assert details["signature_verified"] is True
    audit_id = rec["audit_id"]
    assert isinstance(audit_id, str)
    assert audit_id.startswith("aud_")


def test_audit_ids_are_unique() -> None:
    ids: set[str] = set()
    for i in range(500):
        rec = build_audit_record(
            run_id="r",
            step_id="s",
            action="APPROVE",
            actor={},
            original_state_hash="h",
            patched_state_hash="h",
            json_patch=[],
            nonce=str(i),
        )
        audit_id = rec["audit_id"]
        assert isinstance(audit_id, str)
        ids.add(audit_id)
    assert len(ids) == 500


def test_log_is_append_only_and_ordered() -> None:
    log = AuditLog(MemoryStorage())
    for action in ("APPROVE", "REJECT", "EDIT"):
        log.append(
            build_audit_record(
                run_id="r",
                step_id="s",
                action=action,
                actor={},
                original_state_hash="h",
                patched_state_hash="h",
                json_patch=[],
                nonce=action,
            )
        )
    assert [r["action"] for r in log.entries(run_id="r")] == ["APPROVE", "REJECT", "EDIT"]
    assert not hasattr(log, "delete")
    assert not hasattr(log, "update")


def test_secrets_never_reach_the_audit_log() -> None:
    """A human note or patch value may contain a credential."""
    from chowki.state.redact import Redactor

    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0"
    log = AuditLog(MemoryStorage(), redactor=Redactor(hmac_key=b"k"))
    log.append(
        build_audit_record(
            run_id="r",
            step_id="s",
            action="EDIT",
            actor={},
            original_state_hash="h",
            patched_state_hash="h",
            json_patch=[{"op": "replace", "path": "/key", "value": secret}],
            nonce="n",
        )
    )
    assert secret not in str(log.entries(run_id="r"))


def test_audit_log_preserves_high_entropy_run_id_and_system_metadata() -> None:
    from chowki.state.redact import Redactor

    high_entropy_run_id = "Zk9x2Lq7Rt4vNb8Wm3Ys6Pd1Ae"
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0"
    log = AuditLog(MemoryStorage(), redactor=Redactor(hmac_key=b"k"))

    rec = build_audit_record(
        run_id=high_entropy_run_id,
        step_id="pause#0",
        action="EDIT",
        actor={"user_id": "U123456", "token": secret},
        original_state_hash="sha256:" + "a" * 64,
        patched_state_hash="sha256:" + "b" * 64,
        json_patch=[{"op": "replace", "path": "/key", "value": secret}],
        nonce="n123",
        note="Approved secret " + secret,
    )
    log.append(rec)

    entries = log.entries(run_id=high_entropy_run_id)
    assert len(entries) == 1
    stored = entries[0]

    assert stored["run_id"] == high_entropy_run_id
    assert stored["step_id"] == "pause#0"
    assert stored["action"] == "EDIT"
    assert stored["original_state_hash"] == "sha256:" + "a" * 64
    assert stored["patched_state_hash"] == "sha256:" + "b" * 64
    assert cast(dict[str, Any], stored["verification_details"])["nonce"] == "n123"
    assert secret not in str(stored)


def test_a_patch_value_is_redacted_in_its_destination_keys_context() -> None:
    """A human's edit is only as safe as the key it lands under makes it.

    "hunter2" matches no credential pattern and clears no entropy threshold: the only
    tier that catches it is the key-name tier, and in a JSON Patch op the value sits
    under `"value"`, never under `password`. `redact_patch` supplies the destination key
    as that context, so the recorded op is no weaker than the state it describes.
    """
    from chowki.state.redact import PLACEHOLDER_RE, Redactor

    redacted = redact_patch(
        Redactor(hmac_key=b"k"),
        [
            {"op": "replace", "path": "/password", "value": "hunter2"},
            {"op": "add", "path": "/nested/api_key", "value": "plain"},
            {"op": "replace", "path": "/notes/0", "value": "all fine"},
            {"op": "remove", "path": "/gone"},
        ],
    )

    assert [op["op"] for op in redacted] == ["replace", "add", "replace", "remove"]
    assert [op["path"] for op in redacted] == [
        "/password",
        "/nested/api_key",
        "/notes/0",
        "/gone",
    ]
    assert "value" not in redacted[3]
    for op in redacted[:2]:
        assert PLACEHOLDER_RE.fullmatch(cast(str, op["value"])) is not None
    assert "hunter2" not in str(redacted)
    assert "plain" not in str(redacted)
    # A value under an ordinary key -- an array element here -- is left legible: the log
    # is a governance record, and redaction is for credentials, not for every edit.
    assert redacted[2]["value"] == "all fine"


def test_appending_a_context_redacted_patch_stores_it_unchanged() -> None:
    """`resume()` hashes the state its redacted patch produces, then hands it to the log.

    The log's own redaction pass must therefore be a fixpoint over what `redact_patch`
    returns: the replay re-applies exactly the ops the log stored, so a rewrite here
    would leave `patched_state_hash` describing a document no replay could rebuild.
    """
    from chowki.state.redact import Redactor

    redactor = Redactor(hmac_key=b"k")
    redacted = redact_patch(
        redactor,
        [
            {"op": "replace", "path": "/api_key", "value": "sk-" + "A1b2C3d4E5f6G7h8I9j0"},
            {"op": "replace", "path": "/nested", "value": {"api_key": "plain"}},
            {"op": "replace", "path": "/count", "value": 3},
            {"op": "remove", "path": "/gone"},
        ],
    )

    log = AuditLog(MemoryStorage(), redactor=redactor)
    log.append(
        build_audit_record(
            run_id="r",
            step_id="s",
            action="EDIT",
            actor={},
            original_state_hash="h",
            patched_state_hash="h",
            json_patch=redacted,
            nonce="n",
        )
    )
    assert log.entries(run_id="r")[0]["json_patch"] == redacted

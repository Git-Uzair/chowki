# python/chowki/tests/unit/test_audit.py
from __future__ import annotations

from typing import Any, cast

from chowki.hitl.audit import AuditLog, build_audit_record
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


def test_appending_an_already_redacted_patch_stores_it_unchanged() -> None:
    """`resume()` redacts the human patch once and hashes the state that patch produces.

    The log's own redaction pass must therefore be a fixpoint over a patch that is
    already redacted: if it rewrote the ops, the replay would rebuild a state whose hash
    no longer matched the `patched_state_hash` recorded beside it.
    """
    from chowki.state.redact import Redactor

    redactor = Redactor(hmac_key=b"k")
    raw = [
        {"op": "replace", "path": "/api_key", "value": "sk-" + "A1b2C3d4E5f6G7h8I9j0"},
        {"op": "replace", "path": "/nested", "value": {"api_key": "plain"}},
        {"op": "remove", "path": "/gone"},
    ]
    redacted = redactor.redact(raw)

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

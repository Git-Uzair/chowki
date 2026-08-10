"""Append-only HITL audit log and record builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from chowki.state.redact import Redactor
from chowki.storage.base import StorageAdapter
from chowki.types import JSONObject


def build_audit_record(
    *,
    run_id: str,
    step_id: str,
    action: str,
    actor: JSONObject | None = None,
    original_state_hash: str,
    patched_state_hash: str,
    json_patch: list[dict[str, Any]] | Sequence[Mapping[str, Any]] | None = None,
    nonce: str,
    note: str | None = None,
) -> JSONObject:
    """Build a standard HITL audit record matching governance specification."""
    rec: JSONObject = {
        "audit_id": f"aud_{uuid4().hex[:16]}",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "step_id": step_id,
        "actor": actor or {},
        "action": action,
        "original_state_hash": original_state_hash,
        "patched_state_hash": patched_state_hash,
        "json_patch": list(json_patch) if json_patch is not None else [],
        "verification_details": {
            "signature_type": "chowki_hmac_sha256",
            "nonce": nonce,
            "signature_verified": True,
        },
    }
    if note:
        rec["note"] = note
    return rec


class AuditLog:
    """Append-only governance provenance log wrapping a storage adapter."""

    def __init__(self, storage: StorageAdapter, *, redactor: Redactor | None = None) -> None:
        self._storage = storage
        self._redactor = redactor

    def append(self, record: JSONObject) -> None:
        rec = (
            cast(JSONObject, self._redactor.redact(record))
            if self._redactor is not None
            else record
        )
        self._storage.append_audit(cast("dict[str, object]", rec))

    def entries(self, *, run_id: str | None = None) -> list[JSONObject]:
        return cast(list[JSONObject], self._storage.list_audit(run_id=run_id))

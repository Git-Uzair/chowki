from __future__ import annotations

import copy
import os
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import msgspec

from chowki.errors import ChowkiStorageError
from chowki.state.blobs import make_blob_ref
from chowki.storage.base import SECRET_BYTES
from chowki.types import RunRecord, RunStatus, SnapshotEnvelope, SnapshotKind, StepRecord

if TYPE_CHECKING:
    from chowki.hitl.gateway import GatewayHandle


class MemoryStorage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._runs: dict[str, RunRecord] = {}
        self._steps: dict[tuple[str, str], StepRecord] = {}
        self._snapshots: dict[tuple[str, int], SnapshotEnvelope] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._secrets: dict[str, bytes] = {}
        self._nonces: dict[str, float] = {}
        self._blobs: dict[str, bytes] = {}
        self._audit: list[dict[str, object]] = []
        self._gateway_handles: dict[str, GatewayHandle] = {}

    def _check_closed(self) -> None:
        if self._closed:
            raise ChowkiStorageError("storage adapter is closed")

    def put_run(self, record: RunRecord) -> None:
        with self._lock:
            self._check_closed()
            self._runs[record.run_id] = copy.deepcopy(record)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            self._check_closed()
            res = self._runs.get(run_id)
            return copy.deepcopy(res) if res is not None else None

    def list_runs(self, *, status: RunStatus | None = None) -> list[RunRecord]:
        with self._lock:
            self._check_closed()
            runs = list(self._runs.values())
            if status is not None:
                runs = [r for r in runs if r.status == status]
            return [copy.deepcopy(r) for r in runs]

    def put_step(self, record: StepRecord) -> None:
        with self._lock:
            self._check_closed()
            self._steps[(record.run_id, record.step_id)] = copy.deepcopy(record)

    def get_step(self, run_id: str, step_id: str) -> StepRecord | None:
        with self._lock:
            self._check_closed()
            res = self._steps.get((run_id, step_id))
            return copy.deepcopy(res) if res is not None else None

    def list_steps(self, run_id: str) -> list[StepRecord]:
        with self._lock:
            self._check_closed()
            steps = [s for (r_id, _), s in self._steps.items() if r_id == run_id]
            steps.sort(key=lambda s: s.ordinal)
            return [copy.deepcopy(s) for s in steps]

    def put_snapshot(self, env: SnapshotEnvelope) -> None:
        with self._lock:
            self._check_closed()
            self._snapshots[(env.run_id, env.step_index)] = copy.deepcopy(env)

    def list_snapshots(self, run_id: str) -> list[SnapshotEnvelope]:
        with self._lock:
            self._check_closed()
            envs = [e for (r_id, _), e in self._snapshots.items() if r_id == run_id]
            envs.sort(key=lambda e: e.step_index)
            return [copy.deepcopy(e) for e in envs]

    def snapshots_for_resume(self, run_id: str) -> list[SnapshotEnvelope]:
        with self._lock:
            self._check_closed()
            envs = [e for (r_id, _), e in self._snapshots.items() if r_id == run_id]
            base_indices = [e.step_index for e in envs if e.kind == SnapshotKind.BASE]
            start_idx = max(base_indices) if base_indices else 0
            res = [e for e in envs if e.step_index >= start_idx]
            res.sort(key=lambda e: e.step_index)
            return [copy.deepcopy(e) for e in res]

    def claim_idempotency_key(self, key: str, *, args_hash: str) -> bool:
        with self._lock:
            self._check_closed()
            if key in self._idempotency:
                existing_hash, _ = self._idempotency[key]
                if existing_hash != args_hash:
                    raise ChowkiStorageError("idempotency key reused with a different payload")
                return False
            created_at = datetime.now(UTC).isoformat()
            self._idempotency[key] = (args_hash, created_at)
            return True

    def get_or_create_secret(self, name: str) -> bytes:
        """Return the named secret, minting it on first use.

        It lives exactly as long as this store does, which is the honest answer for an
        in-memory adapter: nothing it holds survives the process either.
        """
        with self._lock:
            self._check_closed()
            return self._secrets.setdefault(name, os.urandom(SECRET_BYTES))

    def consume_nonce(self, nonce: str, *, expires_at_epoch: float | int) -> bool:
        """Claim a nonce, which stays claimed for the lifetime of the store.

        Entries are never dropped on expiry, matching :class:`~chowki.storage.sqlite.SQLiteStorage`.
        """
        with self._lock:
            self._check_closed()
            if nonce in self._nonces:
                return False
            self._nonces[nonce] = float(expires_at_epoch)
            return True

    def put_blob(self, data: bytes) -> str:
        ref = make_blob_ref(data)
        with self._lock:
            self._check_closed()
            self._blobs[ref] = bytes(data)
        return ref

    def get_blob(self, ref: str) -> bytes | None:
        with self._lock:
            self._check_closed()
            return self._blobs.get(ref)

    def append_audit(self, record: dict[str, object]) -> None:
        # Msgpack roundtrip normalizes types (e.g. tuple -> list) and rejects
        # non-encodable objects, ensuring identical behavior to SQLiteStorage.
        data = msgspec.msgpack.encode(record)
        decoded = cast("dict[str, object]", msgspec.msgpack.decode(data))
        with self._lock:
            self._check_closed()
            self._audit.append(decoded)

    def list_audit(self, *, run_id: str | None = None) -> list[dict[str, object]]:
        with self._lock:
            self._check_closed()
            if run_id is None:
                return [copy.deepcopy(r) for r in self._audit]
            return [
                copy.deepcopy(r)
                for r in self._audit
                if isinstance(r.get("run_id"), str) and r.get("run_id") == run_id
            ]

    def put_gateway_handle(self, run_id: str, handle: GatewayHandle) -> None:
        with self._lock:
            self._check_closed()
            self._gateway_handles[run_id] = copy.deepcopy(handle)

    def get_gateway_handle(self, run_id: str) -> GatewayHandle | None:
        with self._lock:
            self._check_closed()
            res = self._gateway_handles.get(run_id)
            return copy.deepcopy(res) if res is not None else None

    def close(self) -> None:
        with self._lock:
            self._closed = True

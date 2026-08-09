from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime

from chowki.errors import ChowkiStorageError
from chowki.state.blobs import BLOB_REF_PREFIX
from chowki.types import RunRecord, RunStatus, SnapshotEnvelope, SnapshotKind, StepRecord


class MemoryStorage:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunRecord] = {}
        self._steps: dict[tuple[str, str], StepRecord] = {}
        self._snapshots: dict[tuple[str, int], SnapshotEnvelope] = {}
        self._idempotency: dict[str, tuple[str, str]] = {}
        self._nonces: dict[str, float] = {}
        self._blobs: dict[str, bytes] = {}
        self._audit: list[dict[str, object]] = []

    def put_run(self, record: RunRecord) -> None:
        with self._lock:
            self._runs[record.run_id] = record

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(self, *, status: RunStatus | None = None) -> list[RunRecord]:
        with self._lock:
            runs = list(self._runs.values())
            if status is not None:
                runs = [r for r in runs if r.status == status]
            return runs

    def put_step(self, record: StepRecord) -> None:
        with self._lock:
            self._steps[(record.run_id, record.step_id)] = record

    def get_step(self, run_id: str, step_id: str) -> StepRecord | None:
        with self._lock:
            return self._steps.get((run_id, step_id))

    def list_steps(self, run_id: str) -> list[StepRecord]:
        with self._lock:
            steps = [s for (r_id, _), s in self._steps.items() if r_id == run_id]
            steps.sort(key=lambda s: s.ordinal)
            return steps

    def put_snapshot(self, env: SnapshotEnvelope) -> None:
        with self._lock:
            self._snapshots[(env.run_id, env.step_index)] = env

    def list_snapshots(self, run_id: str) -> list[SnapshotEnvelope]:
        with self._lock:
            envs = [e for (r_id, _), e in self._snapshots.items() if r_id == run_id]
            envs.sort(key=lambda e: e.step_index)
            return envs

    def snapshots_for_resume(self, run_id: str) -> list[SnapshotEnvelope]:
        with self._lock:
            envs = [e for (r_id, _), e in self._snapshots.items() if r_id == run_id]
            base_indices = [e.step_index for e in envs if e.kind == SnapshotKind.BASE]
            start_idx = max(base_indices) if base_indices else 0
            res = [e for e in envs if e.step_index >= start_idx]
            res.sort(key=lambda e: e.step_index)
            return res

    def claim_idempotency_key(self, key: str, *, args_hash: str) -> bool:
        with self._lock:
            if key in self._idempotency:
                existing_hash, _ = self._idempotency[key]
                if existing_hash != args_hash:
                    raise ChowkiStorageError("idempotency key reused with a different payload")
                return False
            created_at = datetime.now(UTC).isoformat()
            self._idempotency[key] = (args_hash, created_at)
            return True

    def consume_nonce(self, nonce: str, *, expires_at_epoch: float | int) -> bool:
        with self._lock:
            if nonce in self._nonces:
                return False
            self._nonces[nonce] = float(expires_at_epoch)
            return True

    def put_blob(self, data: bytes) -> str:
        ref = BLOB_REF_PREFIX + hashlib.sha256(data).hexdigest()
        with self._lock:
            self._blobs[ref] = data
        return ref

    def get_blob(self, ref: str) -> bytes | None:
        with self._lock:
            return self._blobs.get(ref)

    def append_audit(self, record: dict[str, object]) -> None:
        with self._lock:
            self._audit.append(dict(record))

    def list_audit(self, *, run_id: str | None = None) -> list[dict[str, object]]:
        with self._lock:
            if run_id is None:
                return [dict(r) for r in self._audit]
            return [dict(r) for r in self._audit if r.get("run_id") == run_id]

    def close(self) -> None:
        pass

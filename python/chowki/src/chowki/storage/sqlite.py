from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import msgspec

from chowki.errors import ChowkiStorageError
from chowki.state.blobs import BLOB_REF_PREFIX
from chowki.state.codec import decode_struct, encode_struct
from chowki.types import RunRecord, RunStatus, SnapshotEnvelope, StepRecord

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    workflow TEXT,
    status TEXT,
    blob BLOB
);

CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT,
    step_id TEXT,
    ordinal INTEGER,
    status TEXT,
    blob BLOB,
    PRIMARY KEY (run_id, step_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    run_id TEXT,
    step_index INTEGER,
    kind TEXT,
    blob BLOB,
    PRIMARY KEY (run_id, step_index)
);

CREATE TABLE IF NOT EXISTS blobs (
    ref TEXT PRIMARY KEY,
    data BLOB
);

CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    args_hash TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS nonces (
    nonce TEXT PRIMARY KEY,
    expires_at REAL
);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    blob BLOB
);
"""


class SQLiteStorage:
    def __init__(self, path: Path | str) -> None:
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            str(path_obj),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.executescript(SCHEMA_SQL)

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ChowkiStorageError("storage adapter is closed")
        return self._conn

    def put_run(self, record: RunRecord) -> None:
        conn = self._get_conn()
        blob = encode_struct(record)
        status_str = str(record.status)
        with self._lock:
            conn.execute(
                """
                INSERT INTO runs (run_id, tenant_id, workflow, status, blob)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    tenant_id=excluded.tenant_id,
                    workflow=excluded.workflow,
                    status=excluded.status,
                    blob=excluded.blob
                """,
                (record.run_id, record.tenant_id, record.workflow, status_str, blob),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute("SELECT blob FROM runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return decode_struct(row[0], RunRecord)

    def list_runs(self, *, status: RunStatus | None = None) -> list[RunRecord]:
        conn = self._get_conn()
        with self._lock:
            if status is None:
                cur = conn.execute("SELECT blob FROM runs")
            else:
                status_str = str(status)
                cur = conn.execute("SELECT blob FROM runs WHERE status = ?", (status_str,))
            return [decode_struct(row[0], RunRecord) for row in cur.fetchall()]

    def put_step(self, record: StepRecord) -> None:
        conn = self._get_conn()
        blob = encode_struct(record)
        status_str = str(record.status)
        with self._lock:
            conn.execute(
                """
                INSERT INTO steps (run_id, step_id, ordinal, status, blob)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, step_id) DO UPDATE SET
                    ordinal=excluded.ordinal,
                    status=excluded.status,
                    blob=excluded.blob
                """,
                (record.run_id, record.step_id, record.ordinal, status_str, blob),
            )

    def get_step(self, run_id: str, step_id: str) -> StepRecord | None:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "SELECT blob FROM steps WHERE run_id = ? AND step_id = ?",
                (run_id, step_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return decode_struct(row[0], StepRecord)

    def list_steps(self, run_id: str) -> list[StepRecord]:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "SELECT blob FROM steps WHERE run_id = ? ORDER BY ordinal ASC",
                (run_id,),
            )
            return [decode_struct(row[0], StepRecord) for row in cur.fetchall()]

    def put_snapshot(self, env: SnapshotEnvelope) -> None:
        conn = self._get_conn()
        blob = encode_struct(env)
        kind_str = str(env.kind)
        with self._lock:
            conn.execute(
                """
                INSERT INTO snapshots (run_id, step_index, kind, blob)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id, step_index) DO UPDATE SET
                    kind=excluded.kind,
                    blob=excluded.blob
                """,
                (env.run_id, env.step_index, kind_str, blob),
            )

    def list_snapshots(self, run_id: str) -> list[SnapshotEnvelope]:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                "SELECT blob FROM snapshots WHERE run_id = ? ORDER BY step_index ASC",
                (run_id,),
            )
            return [decode_struct(row[0], SnapshotEnvelope) for row in cur.fetchall()]

    def snapshots_for_resume(self, run_id: str) -> list[SnapshotEnvelope]:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute(
                """
                SELECT blob FROM snapshots
                WHERE run_id = ?
                  AND step_index >= COALESCE(
                      (SELECT MAX(step_index) FROM snapshots WHERE run_id = ? AND kind = 'base'),
                      0
                  )
                ORDER BY step_index ASC
                """,
                (run_id, run_id),
            )
            return [decode_struct(row[0], SnapshotEnvelope) for row in cur.fetchall()]

    def claim_idempotency_key(self, key: str, *, args_hash: str) -> bool:
        conn = self._get_conn()
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            cur = conn.execute(
                """
                INSERT INTO idempotency (key, args_hash, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, args_hash, created_at),
            )
            if cur.rowcount == 1:
                return True

            cur = conn.execute("SELECT args_hash FROM idempotency WHERE key = ?", (key,))
            row = cur.fetchone()
            if row is not None and row[0] != args_hash:
                raise ChowkiStorageError("idempotency key reused with a different payload")
            return False

    def consume_nonce(self, nonce: str, *, expires_at_epoch: float | int) -> bool:
        conn = self._get_conn()
        now = datetime.now(UTC).timestamp()
        with self._lock:
            cur = conn.execute(
                """
                INSERT INTO nonces (nonce, expires_at)
                VALUES (?, ?)
                ON CONFLICT(nonce) DO NOTHING
                """,
                (nonce, float(expires_at_epoch)),
            )
            if cur.rowcount == 0:
                return False
            conn.execute(
                "DELETE FROM nonces WHERE expires_at < ? AND nonce != ?",
                (now, nonce),
            )
            return True

    def put_blob(self, data: bytes) -> str:
        conn = self._get_conn()
        ref = BLOB_REF_PREFIX + hashlib.sha256(data).hexdigest()
        with self._lock:
            conn.execute(
                "INSERT INTO blobs (ref, data) VALUES (?, ?) ON CONFLICT(ref) DO NOTHING",
                (ref, data),
            )
        return ref

    def get_blob(self, ref: str) -> bytes | None:
        conn = self._get_conn()
        with self._lock:
            cur = conn.execute("SELECT data FROM blobs WHERE ref = ?", (ref,))
            row = cur.fetchone()
            if row is None:
                return None
            return cast(bytes, row[0])

    def append_audit(self, record: dict[str, object]) -> None:
        conn = self._get_conn()
        run_id = cast(str | None, record.get("run_id"))
        blob = msgspec.msgpack.encode(record)
        with self._lock:
            conn.execute(
                "INSERT INTO audit (run_id, blob) VALUES (?, ?)",
                (run_id, blob),
            )

    def list_audit(self, *, run_id: str | None = None) -> list[dict[str, object]]:
        conn = self._get_conn()
        with self._lock:
            if run_id is None:
                cur = conn.execute("SELECT blob FROM audit ORDER BY seq ASC")
            else:
                cur = conn.execute(
                    "SELECT blob FROM audit WHERE run_id = ? ORDER BY seq ASC",
                    (run_id,),
                )
            return [
                cast(dict[str, object], msgspec.msgpack.decode(row[0])) for row in cur.fetchall()
            ]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

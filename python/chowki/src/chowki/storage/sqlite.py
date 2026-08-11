"""Embedded SQLite storage adapter implementation.

Concurrency Model & Limitations (Risk R7):
- Configured with WAL mode (`PRAGMA journal_mode=WAL`), a 5 s busy timeout
  (`PRAGMA busy_timeout=5000`), `synchronous=NORMAL`, and process-level write locks
  (`threading.Lock`) to handle single-process concurrency.
- Designed primarily for single-process durability. Multi-process deployment against a single
  SQLite file may encounter `database is locked` under concurrent write contention.
- Multi-process and distributed deployments are intended to use pluggable `StorageAdapter`
  implementations (e.g., Postgres/Redis in Phase 2). Connection pooling is intentionally omitted.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

import msgspec

from chowki.errors import ChowkiStorageError
from chowki.hitl.gateway import GatewayHandle
from chowki.state.blobs import make_blob_ref
from chowki.state.codec import decode_struct, encode_struct
from chowki.storage.base import SECRET_BYTES
from chowki.types import RunRecord, RunStatus, SnapshotEnvelope, StepRecord

#: Only lifecycle value a Phase 1 claim can have; the column exists for later phases.
IDEMPOTENCY_CLAIMED: Final[str] = "claimed"

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
    status TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS secrets (
    name TEXT PRIMARY KEY,
    value BLOB
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

CREATE TABLE IF NOT EXISTS gateway_handles (
    run_id TEXT PRIMARY KEY,
    blob BLOB
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);
CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit (run_id);
"""


class SQLiteStorage:
    """SQLite storage adapter implementation for single-process durability.

    Concurrency Model & Limitations (Risk R7):
    - Uses WAL mode (`PRAGMA journal_mode=WAL`), a 5 s busy timeout (`PRAGMA busy_timeout=5000`),
      `synchronous=NORMAL`, and process-level write locks (`threading.Lock`) to handle
      single-process concurrency.
    - Multi-process deployment against a single SQLite file may encounter `database is locked`
      under write contention.
    - Distributed or multi-process deployments are intended to use pluggable `StorageAdapter`
      implementations (e.g., Postgres/Redis in Phase 2). Connection pooling is intentionally
      omitted.
    """

    def __init__(self, path: Path | str) -> None:
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        #: The file this adapter is bound to. Public because callers that must name the
        #: database on a command line (the CLI `--db` hint) need the file actually in
        #: use, which is not derivable from a config when the adapter was passed in.
        self.db_path: Path = path_obj
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
        blob = encode_struct(record)
        status_str = str(record.status)
        with self._lock:
            conn = self._get_conn()
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
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT blob FROM runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return decode_struct(row[0], RunRecord)

    def list_runs(self, *, status: RunStatus | None = None) -> list[RunRecord]:
        with self._lock:
            conn = self._get_conn()
            if status is None:
                cur = conn.execute("SELECT blob FROM runs")
            else:
                status_str = str(status)
                cur = conn.execute("SELECT blob FROM runs WHERE status = ?", (status_str,))
            return [decode_struct(row[0], RunRecord) for row in cur.fetchall()]

    def put_step(self, record: StepRecord) -> None:
        blob = encode_struct(record)
        status_str = str(record.status)
        with self._lock:
            conn = self._get_conn()
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
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT blob FROM steps WHERE run_id = ? AND step_id = ?",
                (run_id, step_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return decode_struct(row[0], StepRecord)

    def list_steps(self, run_id: str) -> list[StepRecord]:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT blob FROM steps WHERE run_id = ? ORDER BY ordinal ASC",
                (run_id,),
            )
            return [decode_struct(row[0], StepRecord) for row in cur.fetchall()]

    def put_snapshot(self, env: SnapshotEnvelope) -> None:
        blob = encode_struct(env)
        kind_str = str(env.kind)
        with self._lock:
            conn = self._get_conn()
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
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT blob FROM snapshots WHERE run_id = ? ORDER BY step_index ASC",
                (run_id,),
            )
            return [decode_struct(row[0], SnapshotEnvelope) for row in cur.fetchall()]

    def snapshots_for_resume(self, run_id: str) -> list[SnapshotEnvelope]:
        with self._lock:
            conn = self._get_conn()
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

    def max_snapshot_index(self, run_id: str) -> int | None:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT MAX(step_index) FROM snapshots WHERE run_id = ?",
                (run_id,),
            )
            row = cur.fetchone()
            return cast("int | None", row[0]) if row is not None else None

    def claim_idempotency_key(self, key: str, *, args_hash: str) -> bool:
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """
                INSERT INTO idempotency (key, args_hash, status, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, args_hash, IDEMPOTENCY_CLAIMED, created_at),
            )
            if cur.rowcount == 1:
                return True

            cur = conn.execute("SELECT args_hash FROM idempotency WHERE key = ?", (key,))
            row = cur.fetchone()
            if row is not None and row[0] != args_hash:
                raise ChowkiStorageError("idempotency key reused with a different payload")
            return False

    def release_idempotency_key(self, key: str) -> bool:
        """Delete a claim so the step that owns it may execute again.

        This is the operator escape hatch behind :func:`chowki.release_step`; it is
        never called on the normal execution path.
        """
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM idempotency WHERE key = ?", (key,))
            return cur.rowcount == 1

    def get_or_create_secret(self, name: str) -> bytes:
        """Return the named 32-byte secret, minting it on first use.

        The insert-then-read pair makes the first writer's bytes the bytes everyone
        reads, so racing processes agree. Keys derived from this secret (step
        idempotency keys) stay reproducible after a crash because the secret outlives
        the process that minted it.
        """
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO secrets (name, value) VALUES (?, ?) ON CONFLICT(name) DO NOTHING",
                (name, os.urandom(SECRET_BYTES)),
            )
            cur = conn.execute("SELECT value FROM secrets WHERE name = ?", (name,))
            return cast(bytes, cur.fetchone()[0])

    def consume_nonce(self, nonce: str, *, expires_at_epoch: float | int) -> bool:
        """Claim a nonce, which stays claimed for the lifetime of the store.

        Rows are never garbage-collected on expiry: deleting an expired row would make an
        already-consumed nonce replayable, which defeats the point of the table.
        """
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """
                INSERT INTO nonces (nonce, expires_at)
                VALUES (?, ?)
                ON CONFLICT(nonce) DO NOTHING
                """,
                (nonce, float(expires_at_epoch)),
            )
            return cur.rowcount == 1

    def put_blob(self, data: bytes) -> str:
        ref = make_blob_ref(data)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO blobs (ref, data) VALUES (?, ?) ON CONFLICT(ref) DO NOTHING",
                (ref, data),
            )
        return ref

    def get_blob(self, ref: str) -> bytes | None:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT data FROM blobs WHERE ref = ?", (ref,))
            row = cur.fetchone()
            if row is None:
                return None
            return cast(bytes, row[0])

    def append_audit(self, record: dict[str, object]) -> None:
        run_id_val = record.get("run_id")
        run_id_col = run_id_val if isinstance(run_id_val, str) else None
        blob = msgspec.msgpack.encode(record)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO audit (run_id, blob) VALUES (?, ?)",
                (run_id_col, blob),
            )

    def list_audit(self, *, run_id: str | None = None) -> list[dict[str, object]]:
        with self._lock:
            conn = self._get_conn()
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

    def put_gateway_handle(self, run_id: str, handle: GatewayHandle) -> None:
        blob = encode_struct(handle)
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT INTO gateway_handles (run_id, blob)
                VALUES (?, ?)
                ON CONFLICT(run_id) DO UPDATE SET blob=excluded.blob
                """,
                (run_id, blob),
            )

    def get_gateway_handle(self, run_id: str) -> GatewayHandle | None:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT blob FROM gateway_handles WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return decode_struct(row[0], GatewayHandle)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

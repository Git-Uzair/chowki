from __future__ import annotations

from pathlib import Path

import pytest

from chowki.storage.sqlite import SQLiteStorage
from chowki.types import RunRecord

pytestmark = pytest.mark.integration


def _run() -> RunRecord:
    return RunRecord(
        run_id="r1",
        workflow="w",
        tenant_id="t",
        created_at_utc="2026-08-08T06:00:00Z",
        updated_at_utc="2026-08-08T06:00:00Z",
    )


def test_data_survives_reopening_the_database(tmp_path: Path) -> None:
    path = tmp_path / "chowki.db"
    first = SQLiteStorage(path)
    first.put_run(_run())
    first.close()

    second = SQLiteStorage(path)
    assert second.get_run("r1") is not None
    second.close()


def test_schema_is_created_once_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "chowki.db"
    SQLiteStorage(path).close()
    SQLiteStorage(path).close()  # must not raise "table already exists"


def test_parent_directory_is_created(tmp_path: Path) -> None:
    store = SQLiteStorage(tmp_path / "nested" / "deeper" / "chowki.db")
    store.put_run(_run())
    store.close()
    assert (tmp_path / "nested" / "deeper" / "chowki.db").is_file()


def test_concurrent_idempotency_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    """The TOCTOU guarantee from docs/research/03-durable-execution.md:74."""
    import threading

    store = SQLiteStorage(tmp_path / "chowki.db")
    results: list[bool] = []
    lock = threading.Lock()

    def claim() -> None:
        won = store.claim_idempotency_key("shared", args_hash="h")
        with lock:
            results.append(won)

    threads = [threading.Thread(target=claim) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.close()

    assert results.count(True) == 1
    assert results.count(False) == 15

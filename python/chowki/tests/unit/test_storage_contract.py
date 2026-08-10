from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from chowki.errors import ChowkiStorageError
from chowki.storage.base import StorageAdapter
from chowki.storage.memory import MemoryStorage
from chowki.storage.sqlite import SQLiteStorage
from chowki.types import RunRecord, RunStatus, SnapshotKind, StepRecord, StepStatus


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[StorageAdapter]:
    adapter: StorageAdapter = (
        MemoryStorage() if request.param == "memory" else SQLiteStorage(tmp_path / "chowki.db")
    )
    yield adapter
    adapter.close()


def _run(run_id: str = "r1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        workflow="demo",
        tenant_id="t1",
        created_at_utc="2026-08-08T06:00:00Z",
        updated_at_utc="2026-08-08T06:00:00Z",
    )


def test_run_put_get_roundtrip(store: StorageAdapter) -> None:
    store.put_run(_run())
    got = store.get_run("r1")
    assert got is not None
    assert got.workflow == "demo"
    assert got.status is RunStatus.PENDING


def test_get_missing_run_returns_none(store: StorageAdapter) -> None:
    assert store.get_run("nope") is None


def test_run_update_is_last_write_wins(store: StorageAdapter) -> None:
    store.put_run(_run())
    updated = _run()
    updated.status = RunStatus.PAUSED
    store.put_run(updated)
    got = store.get_run("r1")
    assert got is not None and got.status is RunStatus.PAUSED


def test_list_runs_filters_by_status(store: StorageAdapter) -> None:
    a, b = _run("a"), _run("b")
    b.status = RunStatus.PAUSED
    store.put_run(a)
    store.put_run(b)
    assert [r.run_id for r in store.list_runs(status=RunStatus.PAUSED)] == ["b"]


def test_steps_are_ordered_by_ordinal(store: StorageAdapter) -> None:
    store.put_run(_run())
    for i in (2, 0, 1):
        store.put_step(
            StepRecord(
                run_id="r1",
                step_id=f"s#{i}",
                name="s",
                ordinal=i,
                idempotency_key=f"k{i}",
                args_hash="sha256:" + "0" * 64,
                started_at_utc="2026-08-08T06:00:00Z",
                status=StepStatus.COMPLETED,
            )
        )
    assert [s.ordinal for s in store.list_steps("r1")] == [0, 1, 2]


def test_get_step_by_id(store: StorageAdapter) -> None:
    store.put_run(_run())
    store.put_step(
        StepRecord(
            run_id="r1",
            step_id="s#0",
            name="s",
            ordinal=0,
            idempotency_key="k",
            args_hash="sha256:" + "0" * 64,
            started_at_utc="2026-08-08T06:00:00Z",
        )
    )
    assert store.get_step("r1", "s#0") is not None
    assert store.get_step("r1", "missing") is None


def test_snapshots_round_trip_and_preserve_order(store: StorageAdapter) -> None:
    from chowki.state.codec import seal

    store.put_run(_run())
    for i in range(3):
        store.put_snapshot(
            seal(
                {"n": i},
                run_id="r1",
                workflow="demo",
                tenant_id="t1",
                step_index=i,
                kind=SnapshotKind.BASE if i == 0 else SnapshotKind.DELTA,
            )
        )
    envs = store.list_snapshots("r1")
    assert [e.step_index for e in envs] == [0, 1, 2]


def test_snapshots_since_last_base(store: StorageAdapter) -> None:
    """Warm resume only needs the newest base plus the deltas after it."""
    from chowki.state.codec import seal

    store.put_run(_run())
    kinds = [SnapshotKind.BASE, SnapshotKind.DELTA, SnapshotKind.BASE, SnapshotKind.DELTA]
    for i, kind in enumerate(kinds):
        store.put_snapshot(
            seal({"n": i}, run_id="r1", workflow="demo", tenant_id="t1", step_index=i, kind=kind)
        )
    envs = store.snapshots_for_resume("r1")
    assert [e.step_index for e in envs] == [2, 3]


def test_idempotency_claim_is_atomic_and_single_winner(store: StorageAdapter) -> None:
    assert store.claim_idempotency_key("key-1", args_hash="h1") is True
    assert store.claim_idempotency_key("key-1", args_hash="h1") is False


def test_idempotency_key_reuse_with_a_different_payload_is_rejected(
    store: StorageAdapter,
) -> None:
    store.claim_idempotency_key("key-2", args_hash="h1")
    with pytest.raises(ChowkiStorageError, match="payload"):
        store.claim_idempotency_key("key-2", args_hash="DIFFERENT")


def test_a_named_secret_is_minted_once_and_returned_thereafter(store: StorageAdapter) -> None:
    first = store.get_or_create_secret("resume")
    assert len(first) == 32
    assert store.get_or_create_secret("resume") == first
    assert store.get_or_create_secret("other") != first


def test_a_sqlite_secret_outlives_the_adapter_that_minted_it(tmp_path: Path) -> None:
    """Durability is the whole point: a new process gets a new adapter, not new bytes."""
    path = tmp_path / "secrets.db"
    first = SQLiteStorage(path)
    minted = first.get_or_create_secret("resume")
    first.close()

    second = SQLiteStorage(path)
    assert second.get_or_create_secret("resume") == minted
    second.close()


def test_nonce_is_single_use(store: StorageAdapter) -> None:
    assert store.consume_nonce("n1", expires_at_epoch=4_102_444_800) is True
    assert store.consume_nonce("n1", expires_at_epoch=4_102_444_800) is False


def test_blob_put_get(store: StorageAdapter) -> None:
    ref = store.put_blob(b"a large prompt")
    assert store.get_blob(ref) == b"a large prompt"
    assert store.get_blob("ref:sha256:" + "0" * 64) is None


def test_audit_log_is_append_only(store: StorageAdapter) -> None:
    store.append_audit({"audit_id": "a1", "action": "APPROVE"})
    store.append_audit({"audit_id": "a2", "action": "REJECT"})
    records = store.list_audit()
    assert [r["audit_id"] for r in records] == ["a1", "a2"]
    assert not hasattr(store, "delete_audit")


def test_mutating_record_after_put_run_does_not_alter_stored_state(
    store: StorageAdapter,
) -> None:
    rec = _run("r_mutate")
    rec.status = RunStatus.PENDING
    store.put_run(rec)
    rec.status = RunStatus.FAILED
    stored = store.get_run("r_mutate")
    assert stored is not None
    assert stored.status is RunStatus.PENDING


def test_consume_nonce_on_expired_nonce_twice_returns_true_then_false(
    store: StorageAdapter,
) -> None:
    first = store.consume_nonce("exp_nonce", expires_at_epoch=0)
    second = store.consume_nonce("exp_nonce", expires_at_epoch=0)
    assert (first, second) == (True, False)


def test_expired_nonce_stays_consumed_after_a_later_unrelated_consume(
    store: StorageAdapter,
) -> None:
    """A consumed nonce must never become replayable, expired or not."""
    first = store.consume_nonce("A", expires_at_epoch=0)
    second = store.consume_nonce("B", expires_at_epoch=4_000_000_000)
    third = store.consume_nonce("A", expires_at_epoch=0)
    assert (first, second, third) == (True, True, False)


def test_calling_method_after_close_raises_storage_error(
    store: StorageAdapter,
) -> None:
    store.close()
    with pytest.raises(ChowkiStorageError):
        store.get_run("r1")
    with pytest.raises(ChowkiStorageError):
        store.put_run(_run("r1"))
    with pytest.raises(ChowkiStorageError):
        store.list_runs()
    with pytest.raises(ChowkiStorageError):
        store.consume_nonce("n1", expires_at_epoch=4_102_444_800)
    with pytest.raises(ChowkiStorageError):
        store.put_blob(b"data")


def test_audit_record_normalizes_tuples_and_rejects_unencodable_objects(
    store: StorageAdapter,
) -> None:
    store.append_audit({"run_id": "r1", "tags": ("a", "b")})
    records = store.list_audit()
    assert records[0]["tags"] == ["a", "b"]

    with pytest.raises((TypeError, Exception)):
        store.append_audit({"obj": object()})


def test_audit_list_filter_ignores_non_string_run_id(
    store: StorageAdapter,
) -> None:
    store.append_audit({"run_id": "42"})
    store.append_audit({"run_id": 42})
    matched = store.list_audit(run_id="42")
    assert len(matched) == 1
    assert matched[0]["run_id"] == "42"


def test_gateway_handle_roundtrip(store: StorageAdapter) -> None:
    from chowki.hitl.gateway import GatewayHandle

    handle = GatewayHandle(channel="slack", message_id="1234.5678", conversation_id="C123")
    store.put_gateway_handle("r1", handle)
    got = store.get_gateway_handle("r1")
    assert got == handle
    assert store.get_gateway_handle("missing") is None

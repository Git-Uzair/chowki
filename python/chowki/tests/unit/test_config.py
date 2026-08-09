from __future__ import annotations

import base64
from pathlib import Path

import pytest

from chowki.config import ChowkiConfig, ChowkiEngine, configure, get_engine, reset_engine
from chowki.storage.memory import MemoryStorage
from chowki.storage.sqlite import SQLiteStorage


def test_default_engine_uses_sqlite_at_the_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    reset_engine()
    engine = get_engine()
    assert isinstance(engine.storage, SQLiteStorage)
    assert (tmp_path / ".chowki" / "chowki.db").is_file()
    engine.close()
    reset_engine()


def test_encryption_is_off_by_default() -> None:
    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))
    assert engine.pipeline_for("r").__class__.__name__ == "SnapshotPipeline"
    assert engine.keyring is None
    engine.close()


def test_encryption_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from chowki.errors import ChowkiConfigError

    monkeypatch.delenv("CHOWKI_MASTER_KEY", raising=False)
    with pytest.raises(ChowkiConfigError, match="CHOWKI_MASTER_KEY"):
        ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), encrypt_at_rest=True))


def test_encryption_picks_up_the_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOWKI_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), encrypt_at_rest=True))
    assert engine.keyring is not None
    engine.close()


def test_redaction_key_is_stable_within_an_engine() -> None:
    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))
    a = engine.redactor.placeholder("openai", "sk-abc")
    b = engine.redactor.placeholder("openai", "sk-abc")
    assert a == b
    engine.close()


def test_configure_replaces_the_process_engine() -> None:
    reset_engine()
    store = MemoryStorage()
    configure(storage=store)
    assert get_engine().storage is store
    reset_engine()


def test_snapshots_are_written_to_storage_through_the_sink() -> None:
    store = MemoryStorage()
    engine = ChowkiEngine(ChowkiConfig(storage=store))
    pipe = engine.pipeline_for("r1")
    pipe.snapshot({"a": 1}, run_id="r1", workflow="w", step_index=0)
    assert len(store.list_snapshots("r1")) == 1
    engine.close()

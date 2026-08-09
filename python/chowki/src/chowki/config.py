from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chowki.errors import ChowkiConfigError
from chowki.state.blobs import BlobStore
from chowki.state.crypto import KeyRing
from chowki.state.pipeline import SnapshotPipeline
from chowki.state.redact import Redactor
from chowki.storage import DEFAULT_DB_PATH, SQLiteStorage, StorageAdapter

if TYPE_CHECKING:
    from chowki.guardrails.config import GuardrailConfig  # type: ignore[import-not-found]
    from chowki.hitl.gateway import ChannelGateway  # type: ignore[import-not-found]


@dataclass(slots=True)
class ChowkiConfig:
    storage: StorageAdapter | None = None
    tenant_id: str = "default"
    encrypt_at_rest: bool = False
    keyring: KeyRing | None = None
    redaction_hmac_key: bytes | None = None
    resume_secret: bytes | None = None
    guardrails: GuardrailConfig | None = None
    gateway: ChannelGateway | None = None
    blob_threshold_bytes: int = 4096
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)


class ChowkiEngine:
    """Central engine assembling state components and storage adapters."""

    def __init__(self, config: ChowkiConfig | None = None) -> None:
        self._config = config if config is not None else ChowkiConfig()

        if self._config.storage is not None:
            self.storage: StorageAdapter = self._config.storage
        else:
            self.storage = SQLiteStorage(self._config.db_path)

        if self._config.keyring is not None:
            self.keyring: KeyRing | None = self._config.keyring
        elif self._config.encrypt_at_rest:
            if "CHOWKI_MASTER_KEY" not in os.environ:
                raise ChowkiConfigError(
                    "encrypt_at_rest requires CHOWKI_MASTER_KEY or an explicit keyring"
                )
            self.keyring = KeyRing.from_env()
        else:
            self.keyring = None

        self.redactor: Redactor = Redactor(
            hmac_key=self._config.redaction_hmac_key or os.urandom(32)
        )
        self.blobs: BlobStore = BlobStore()
        self._pipelines: dict[str, SnapshotPipeline] = {}

    @property
    def resume_secret(self) -> bytes:
        return self._config.resume_secret or self.redactor.hmac_key

    def pipeline_for(self, run_id: str) -> SnapshotPipeline:
        """Get or create memoised SnapshotPipeline for a given run_id."""
        if run_id not in self._pipelines:
            use_keyring = self.keyring if self._config.encrypt_at_rest else None
            self._pipelines[run_id] = SnapshotPipeline(
                redactor=self.redactor,
                blobs=self.blobs,
                tenant_id=self._config.tenant_id,
                keyring=use_keyring,
                sink=self.storage.put_snapshot,
                blob_threshold_bytes=self._config.blob_threshold_bytes,
            )
        return self._pipelines[run_id]

    def drop_pipeline(self, run_id: str) -> None:
        """Remove pipeline for a terminal run_id to avoid leaking memory."""
        self._pipelines.pop(run_id, None)

    def close(self) -> None:
        """Close underlying storage adapter and clear memoised pipelines."""
        self.storage.close()
        self._pipelines.clear()


_engine: ChowkiEngine | None = None


def get_engine() -> ChowkiEngine:
    """Retrieve process-global engine, creating a default one if absent."""
    global _engine
    if _engine is None:
        _engine = ChowkiEngine()
    return _engine


def configure(**kwargs: Any) -> ChowkiEngine:
    """Build a ChowkiConfig from kwargs, replace the process engine, closing any previous engine."""
    global _engine
    config = ChowkiConfig(**kwargs)
    new_engine = ChowkiEngine(config)
    old_engine = _engine
    _engine = new_engine
    if old_engine is not None:
        old_engine.close()
    return new_engine


def reset_engine() -> None:
    """Close and clear process-global engine for clean test isolation."""
    global _engine
    if _engine is not None:
        _engine.close()
        _engine = None

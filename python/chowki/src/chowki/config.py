from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from chowki.errors import ChowkiConfigError
from chowki.guardrails.config import GuardrailConfig
from chowki.hitl.tokens import TokenIssuer
from chowki.state.blobs import BlobStore
from chowki.state.crypto import KeyRing
from chowki.state.pipeline import SnapshotPipeline
from chowki.state.redact import Redactor
from chowki.storage import DEFAULT_DB_PATH, SQLiteStorage, StorageAdapter

if TYPE_CHECKING:
    from chowki.hitl.gateway import ChannelGateway

#: Slot name (not a credential) under which the store keeps the resume HMAC bytes.
_RESUME_SLOT: Final[str] = "resume"

#: Slot name under which the store keeps the redaction-placeholder HMAC bytes.
_REDACTION_SLOT: Final[str] = "redaction"


@dataclass(slots=True)
class ChowkiConfig:
    storage: StorageAdapter | None = None
    tenant_id: str = "default"
    encrypt_at_rest: bool = False
    keyring: KeyRing | None = None
    redaction_hmac_key: bytes | None = None
    resume_secret: bytes | str | None = None
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    gateway: ChannelGateway | None = None
    blob_threshold_bytes: int = 4096
    db_path: Path = field(default_factory=lambda: DEFAULT_DB_PATH)
    tracing_enabled: bool = False


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

        # Persisted, not per-process random: the placeholder short-hash is an HMAC of
        # the secret under this key, and stored state carries those placeholders, so a
        # key that changed on every restart would redact the same secret to a different
        # placeholder in every process -- breaking placeholder correlation across
        # resumes and across-the-wire hash comparisons for otherwise identical state.
        self.redactor: Redactor = Redactor(
            hmac_key=self._config.redaction_hmac_key
            or self.storage.get_or_create_secret(_REDACTION_SLOT)
        )
        # Backed by the storage adapter so blobs extracted from state are as
        # durable as the snapshots that reference them (a ref a fresh process
        # cannot inline would make warm resume fail with a missing blob).
        self.blobs: BlobStore = BlobStore(storage=self.storage)
        self.gateway: ChannelGateway | None = self._config.gateway
        self._pipelines: dict[str, SnapshotPipeline] = {}
        self.pending_resume_state: dict[str, tuple[str, dict[str, Any]]] = {}

        # Falsiness, not `is None`: b"" and "" are not secrets, and hmac.new(b"", ...) is
        # forgeable by anyone who guesses the config was left blank.
        if not self._config.resume_secret:
            token_secret = os.urandom(32)
            self._resume_secret = None
            # stdlib warnings, not structlog: this fires on every engine, and structlog's
            # default logger prints to stdout, which subprocess probes read verbatim.
            warnings.warn(
                "resume_secret not configured; resume tokens are signed with an ephemeral "
                "per-process key and will not verify after a restart or deploy",
                UserWarning,
                stacklevel=2,
            )
        elif isinstance(self._config.resume_secret, str):
            token_secret = self._config.resume_secret.encode("utf-8")
            self._resume_secret = token_secret
        else:
            token_secret = self._config.resume_secret
            self._resume_secret = token_secret

        self.tokens: TokenIssuer = TokenIssuer(secret=token_secret, storage=self.storage)

    @property
    def config(self) -> ChowkiConfig:
        return self._config

    @property
    def resume_secret(self) -> bytes:
        """HMAC secret behind step idempotency keys.

        It is read from the store rather than generated per process: the crash a step's
        idempotency guard exists for is exactly the event that would otherwise change
        the secret, leaving the recovering process unable to recognise its own claim
        (``docs/research/03-durable-execution.md:73``). Cached because ``_begin`` asks
        for it on every step.
        """
        if self._resume_secret is None:
            self._resume_secret = self.storage.get_or_create_secret(_RESUME_SLOT)
        return self._resume_secret

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
                tracing_enabled=self._config.tracing_enabled,
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


def active_engine() -> ChowkiEngine | None:
    """Return the process-global engine, or None when none was installed yet.

    Unlike :func:`get_engine` this never creates one: code that merely *describes* the
    current setup (the console pause hint) must not install a default engine, because
    doing so opens ``.chowki/chowki.db`` in the working directory as a side effect.
    """
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

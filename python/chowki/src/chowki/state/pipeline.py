"""Hot-path snapshot pipeline for chowki state management.

Assembles redaction, blob extraction, base/delta selection, MessagePack encoding,
SHA-256 hashing, optional AES-256-GCM encryption, and dispatch.

Invariants and non-negotiables:
1. Raw state is never retained by the pipeline. `_RunState.redacted_current` holds the
   redacted tree only.
2. `restore()` returns redacted state. A caller who needs the original secret must
   re-supply it from the environment.
3. The pipeline is NOT thread-safe per run id. One run executes on one task at a time;
   concurrency across different run ids is safe because state is keyed by run id.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from chowki.errors import (
    ChowkiStateError,
    DecryptionError,
    SchemaVersionError,
    SnapshotIntegrityError,
)
from chowki.state.blobs import BlobStore, extract_blobs, inline_blobs
from chowki.state.canonical import hash_bytes
from chowki.state.codec import decode_state, encode_state, migrate
from chowki.state.crypto import KeyRing, decrypt, encrypt
from chowki.state.delta import DeltaChain, Patch, make_patch
from chowki.state.redact import Redactor
from chowki.types import SCHEMA_VERSION, JSONValue, SnapshotEnvelope, SnapshotKind


@dataclass(slots=True)
class _RunState:
    base: JSONValue
    base_bytes: int
    chain: DeltaChain
    last_hash: str
    redacted_current: JSONValue
    stripped_current: JSONValue


class SnapshotPipeline:
    """Hot-path per-step snapshot pipeline executing:

    redact -> blob -> delta/base -> encode -> hash -> encrypt -> dispatch.
    """

    def __init__(
        self,
        *,
        redactor: Redactor,
        blobs: BlobStore,
        tenant_id: str = "default",
        keyring: KeyRing | None = None,
        sink: Callable[[SnapshotEnvelope], None] | None = None,
        blob_threshold_bytes: int = 4096,
    ) -> None:
        self._redactor = redactor
        self._blobs = blobs
        self._tenant_id = tenant_id
        self._keyring = keyring
        self._sink = sink
        self._blob_threshold_bytes = blob_threshold_bytes
        self._runs: dict[str, _RunState] = {}

    def snapshot(
        self, state: JSONValue, *, run_id: str, workflow: str, step_index: int
    ) -> SnapshotEnvelope:
        """Process state through hot-path pipeline and produce a frozen SnapshotEnvelope."""
        # 1. Redact
        redacted = self._redactor.redact(state)

        # 2. Blob-extract
        stripped = extract_blobs(redacted, self._blobs, threshold_bytes=self._blob_threshold_bytes)

        # 3. Choose BASE vs DELTA
        prev_hash: str | None = None
        if run_id not in self._runs:
            kind = SnapshotKind.BASE
            body: JSONValue = stripped
        else:
            run_state = self._runs[run_id]
            prev_hash = run_state.last_hash
            prev_stripped = run_state.stripped_current
            if run_state.chain.needs_compaction(run_state.base_bytes):
                kind = SnapshotKind.BASE
                body = stripped
            else:
                kind = SnapshotKind.DELTA
                body = make_patch(prev_stripped, stripped)

        # 4. Encode
        payload = encode_state(body)
        unencrypted_len = len(payload)

        # 5. Hash over unencrypted payload
        state_hash = hash_bytes(payload)

        # 6. Encrypt if keyring supplied
        key_id: str | None = None
        nonce: bytes | None = None
        if self._keyring is not None:
            aad = SnapshotEnvelope.format_aad(self._tenant_id, run_id, SCHEMA_VERSION)
            payload, key_id, nonce = encrypt(payload, self._keyring, aad=aad)

        # 7. Assemble frozen SnapshotEnvelope and update _RunState
        created_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        env = SnapshotEnvelope(
            v=SCHEMA_VERSION,
            run_id=run_id,
            workflow=workflow,
            tenant_id=self._tenant_id,
            step_index=step_index,
            kind=kind,
            created_at_utc=created_at_utc,
            state_hash=state_hash,
            payload=payload,
            parent_hash=prev_hash,
            key_id=key_id,
            nonce=nonce,
            codec="msgpack",
        )

        if kind is SnapshotKind.BASE:
            base_bytes = unencrypted_len
            chain = DeltaChain(base=stripped)
            self._runs[run_id] = _RunState(
                base=stripped,
                base_bytes=base_bytes,
                chain=chain,
                last_hash=state_hash,
                redacted_current=redacted,
                stripped_current=stripped,
            )
        else:
            run_state = self._runs[run_id]
            patch = cast(Patch, body)
            run_state.chain.append(patch)
            run_state.last_hash = state_hash
            run_state.redacted_current = redacted
            run_state.stripped_current = stripped

        # 8. Dispatch
        self.dispatch(env)

        return env

    def restore(self, *, run_id: str) -> JSONValue:
        """Retrieve redacted state for active run_id from in-memory pipeline state."""
        if run_id not in self._runs:
            raise ChowkiStateError(f"unknown run_id {run_id!r}")
        run_state = self._runs[run_id]
        reconstructed_stripped = run_state.chain.materialize()
        return cast(JSONValue, inline_blobs(reconstructed_stripped, self._blobs))

    def dispatch(self, env: SnapshotEnvelope) -> None:
        """Invoke sink callback if present."""
        if self._sink is not None:
            self._sink(env)

    def load(self, envelopes: Sequence[SnapshotEnvelope]) -> JSONValue:
        """Cold-path reconstruction from sequence of envelopes."""
        if not envelopes:
            raise ChowkiStateError("cannot load state from empty envelope sequence")

        chain: DeltaChain | None = None
        run_id = envelopes[-1].run_id
        last_hash = envelopes[-1].state_hash

        for env in envelopes:
            if env.v > SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"snapshot schema v{env.v} is newer than this chowki build "
                    f"(v{SCHEMA_VERSION}); upgrade chowki"
                )

            if env.key_id is not None or env.nonce is not None:
                if self._keyring is None:
                    raise DecryptionError("cannot decrypt snapshot envelope without a keyring")
                if env.key_id is None or env.nonce is None:
                    raise DecryptionError("snapshot envelope missing key_id or nonce")
                aad = env.aad()
                payload = decrypt(
                    env.payload, self._keyring, key_id=env.key_id, nonce=env.nonce, aad=aad
                )
            else:
                payload = env.payload

            computed_hash = hash_bytes(payload)
            if computed_hash != env.state_hash:
                raise SnapshotIntegrityError(
                    f"state_hash mismatch: envelope has {env.state_hash}, "
                    f"computed payload hash is {computed_hash}"
                )

            body = decode_state(payload)
            if env.v < SCHEMA_VERSION and isinstance(body, dict):
                body = migrate(body, from_version=env.v, to_version=SCHEMA_VERSION)

            if env.kind is SnapshotKind.BASE:
                chain = DeltaChain(base=body)
            elif env.kind is SnapshotKind.DELTA:
                if chain is None:
                    raise ChowkiStateError("delta snapshot encountered before base snapshot")
                patch = cast(Patch, body)
                chain.append(patch)

        if chain is None:
            raise ChowkiStateError("no valid base snapshot found in envelopes")

        stripped_state = chain.materialize()
        restored_state = cast(JSONValue, inline_blobs(stripped_state, self._blobs))

        base_payload = encode_state(chain.base)
        base_bytes = len(base_payload)
        self._runs[run_id] = _RunState(
            base=chain.base,
            base_bytes=base_bytes,
            chain=chain,
            last_hash=last_hash,
            redacted_current=restored_state,
            stripped_current=stripped_state,
        )

        return restored_state

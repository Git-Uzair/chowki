"""Hot-path snapshot pipeline for chowki state management.

Assembles redaction, blob extraction, base/delta selection, MessagePack encoding,
SHA-256 hashing, optional AES-256-GCM encryption, and dispatch.

Invariants and non-negotiables:
1. Raw state is never retained by the pipeline. `_RunState.stripped_current` holds the
   redacted, blob-stripped tree only.
2. Nothing the pipeline retains is reachable from the caller. The redaction walk rebuilds
   every container, so a caller that keeps mutating the dict it handed to `snapshot()`
   can neither corrupt the next delta nor push a secret into a stored snapshot.
3. `restore()` returns redacted state. A caller who needs the original secret must
   re-supply it from the environment.
4. The pipeline is NOT thread-safe per run id. One run executes on one task at a time;
   concurrency across different run ids is safe because state is keyed by run id.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from msgspec.structs import replace as msgspec_replace

from chowki.errors import (
    ChowkiStateError,
    DecryptionError,
)
from chowki.state.blobs import BlobStore, inline_blobs
from chowki.state.canonical import hash_bytes
from chowki.state.codec import encode_state, unseal
from chowki.state.crypto import KeyRing, decrypt, encrypt
from chowki.state.delta import DeltaChain, Patch, make_patch
from chowki.state.redact import Redactor
from chowki.types import SCHEMA_VERSION, JSONValue, SnapshotEnvelope, SnapshotKind


@dataclass(slots=True)
class _RunState:
    """Pipeline-owned state for one run.

    The plan's `redacted_current` is realised as `stripped_current`: the redacted tree
    after blob extraction. Retaining the pre-extraction tree as well would double the
    per-run memory for a field nothing reads.
    """

    base_bytes: int
    chain: DeltaChain
    last_hash: str
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
        # 1./2. Redact, then blob-extract, in one traversal. Order is preserved per leaf
        # — a large secret is redacted first, so its short placeholder never becomes a
        # blob — but a 1 MiB state is walked once instead of twice, and the walk hands
        # back containers the caller does not share.
        stripped: JSONValue = self._redactor.redact(
            state, blobs=self._blobs, blob_threshold_bytes=self._blob_threshold_bytes
        )

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
            self._runs[run_id] = _RunState(
                base_bytes=unencrypted_len,
                chain=DeltaChain(base=stripped),
                last_hash=state_hash,
                stripped_current=stripped,
            )
        else:
            run_state = self._runs[run_id]
            patch = cast(Patch, body)
            run_state.chain.append(patch)
            run_state.last_hash = state_hash
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
            if env.key_id is not None or env.nonce is not None:
                if self._keyring is None:
                    raise DecryptionError("cannot decrypt snapshot envelope without a keyring")
                if env.key_id is None or env.nonce is None:
                    raise DecryptionError("snapshot envelope missing key_id or nonce")
                aad = env.aad()
                payload = decrypt(
                    env.payload, self._keyring, key_id=env.key_id, nonce=env.nonce, aad=aad
                )
                env = msgspec_replace(env, payload=payload)

            body = unseal(env)

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

        self._runs[run_id] = _RunState(
            base_bytes=len(encode_state(chain.base)),
            chain=chain,
            last_hash=last_hash,
            stripped_current=stripped_state,
        )

        return restored_state

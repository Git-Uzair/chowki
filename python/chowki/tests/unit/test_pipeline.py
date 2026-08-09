# python/chowki/tests/unit/test_pipeline.py
from __future__ import annotations

import pytest

from chowki.errors import ChowkiStateError, DecryptionError, SnapshotIntegrityError
from chowki.state.blobs import BlobStore
from chowki.state.crypto import KeyRing
from chowki.state.pipeline import SnapshotPipeline
from chowki.state.redact import PLACEHOLDER_RE, Redactor
from chowki.types import SnapshotEnvelope, SnapshotKind

SECRET = "sk-" + "A1b2C3d4E5f6G7h8I9j0"


def make_pipeline(**kw: object) -> SnapshotPipeline:
    return SnapshotPipeline(
        redactor=Redactor(hmac_key=b"test"),
        blobs=BlobStore(),
        keyring=kw.pop("keyring", None),  # type: ignore[arg-type]
        tenant_id="t1",
        **kw,  # type: ignore[arg-type]
    )


def test_first_snapshot_is_a_base() -> None:
    pipe = make_pipeline()
    env = pipe.snapshot({"a": 1}, run_id="r", workflow="w", step_index=0)
    assert env.kind is SnapshotKind.BASE
    assert env.parent_hash is None
    assert pipe.restore(run_id="r") == {"a": 1}


def test_second_snapshot_is_a_delta_linked_to_its_parent() -> None:
    """Plan deviation (Task 11): the plan's `{"a": 1}` -> `{"a": 2}` cannot satisfy the
    plan's own `len(second.payload) < len(first.payload)`. A one-key base encodes to 4
    bytes of MessagePack while the RFC 6902 patch that replaces its value encodes to 28,
    so a delta is only smaller once the base carries more than the patch describes. The
    padding restores the property the assertion is there to prove.
    """
    pipe = make_pipeline()
    base_state = {"a": 1, "data": "x" * 200}
    next_state = {"a": 2, "data": "x" * 200}
    first = pipe.snapshot(base_state, run_id="r", workflow="w", step_index=0)
    second = pipe.snapshot(next_state, run_id="r", workflow="w", step_index=1)
    assert second.kind is SnapshotKind.DELTA
    assert second.parent_hash == first.state_hash
    assert len(second.payload) < len(first.payload)
    assert pipe.restore(run_id="r") == next_state


def test_secrets_never_reach_the_payload() -> None:
    pipe = make_pipeline()
    env = pipe.snapshot({"api_key": SECRET}, run_id="r", workflow="w", step_index=0)
    assert SECRET.encode() not in env.payload
    restored = pipe.restore(run_id="r")
    assert isinstance(restored, dict)
    assert PLACEHOLDER_RE.fullmatch(str(restored["api_key"]))


def test_secrets_are_redacted_before_hashing() -> None:
    """The state hash must cover redacted state, or the hash itself leaks by oracle."""
    pipe_a, pipe_b = make_pipeline(), make_pipeline()
    a = pipe_a.snapshot({"api_key": SECRET}, run_id="r", workflow="w", step_index=0)
    b = pipe_b.snapshot({"api_key": SECRET}, run_id="r", workflow="w", step_index=0)
    assert a.state_hash == b.state_hash


def test_encryption_roundtrip_when_a_keyring_is_supplied() -> None:
    pipe = make_pipeline(keyring=KeyRing.from_key(b"k" * 32, key_id="k1"))
    env = pipe.snapshot({"note": "plaintext marker"}, run_id="r", workflow="w", step_index=0)
    assert env.key_id == "k1"
    assert env.nonce is not None
    assert b"plaintext marker" not in env.payload
    assert pipe.restore(run_id="r") == {"note": "plaintext marker"}


def test_no_keyring_means_no_encryption_metadata() -> None:
    env = make_pipeline().snapshot({"a": 1}, run_id="r", workflow="w", step_index=0)
    assert env.key_id is None
    assert env.nonce is None


def test_compaction_forces_a_new_base_at_depth_50() -> None:
    """Plan deviation (Task 11): with the plan's `{"n": i}` the depth rule never runs.
    A 4-byte base is exceeded by a 28-byte patch, so `should_compact`'s size rule
    (delta_bytes > 20% of base_bytes, Task 9) fires on every second step and the kinds
    alternate BASE/DELTA forever. Padding the state makes the base large enough for the
    depth-50 rule to be the one under test.
    """
    pipe = make_pipeline()
    # Integer padding: nothing here reaches the blob threshold, so base_bytes stays ~12 KB.
    state_padding = {f"k{j}": j for j in range(1000)}
    kinds = [
        pipe.snapshot({"n": i, **state_padding}, run_id="r", workflow="w", step_index=i).kind
        for i in range(52)
    ]
    assert kinds[0] is SnapshotKind.BASE
    assert kinds[1] is SnapshotKind.DELTA
    assert kinds[50] is SnapshotKind.DELTA
    assert kinds[51] is SnapshotKind.BASE  # chain reset after 50 deltas
    assert pipe.restore(run_id="r") == {"n": 51, **state_padding}


def test_large_strings_are_extracted_to_blobs() -> None:
    pipe = make_pipeline()
    prompt = "S" * 9000
    env = pipe.snapshot({"system_prompt": prompt}, run_id="r", workflow="w", step_index=0)
    assert prompt.encode() not in env.payload
    assert pipe.restore(run_id="r") == {"system_prompt": prompt}


def test_dispatch_receives_every_envelope() -> None:
    seen: list[SnapshotEnvelope] = []
    pipe = make_pipeline(sink=seen.append)
    pipe.snapshot({"a": 1}, run_id="r", workflow="w", step_index=0)
    pipe.snapshot({"a": 2}, run_id="r", workflow="w", step_index=1)
    assert len(seen) == 2
    assert [e.step_index for e in seen] == [0, 1]


def test_restore_of_an_unknown_run_raises() -> None:
    with pytest.raises(ChowkiStateError):
        make_pipeline().restore(run_id="nope")


def test_caller_may_mutate_its_state_dict_between_steps() -> None:
    """The pipeline must own what it retains, or the next delta diffs against itself."""
    pipe = make_pipeline()
    state: dict[str, object] = {"a": 1, "data": "x" * 200}
    first = pipe.snapshot(state, run_id="r", workflow="w", step_index=0)
    state["a"] = 2
    second = pipe.snapshot(state, run_id="r", workflow="w", step_index=1)

    assert second.kind is SnapshotKind.DELTA
    loaded = pipe.load([first, second])
    assert isinstance(loaded, dict)
    assert loaded["a"] == 2
    restored = pipe.restore(run_id="r")
    assert isinstance(restored, dict)
    assert restored["a"] == 2


def test_a_secret_written_into_the_caller_dict_after_a_snapshot_stays_out() -> None:
    """Module invariant 1: raw state is never retained, not even by aliasing."""
    pipe = make_pipeline()
    state: dict[str, object] = {"a": 1, "nested": {"data": "x" * 200}}
    pipe.snapshot(state, run_id="r", workflow="w", step_index=0)

    state["leaked"] = SECRET
    nested = state["nested"]
    assert isinstance(nested, dict)
    nested["leaked"] = SECRET

    assert SECRET not in repr(pipe.restore(run_id="r"))


def test_load_cold_path_reconstruction() -> None:
    keyring = KeyRing.from_key(b"k" * 32, key_id="k1")
    pipe = make_pipeline(keyring=keyring)
    env0 = pipe.snapshot(
        {"count": 0, "secret": SECRET}, run_id="r_cold", workflow="w", step_index=0
    )
    env1 = pipe.snapshot(
        {"count": 1, "secret": SECRET}, run_id="r_cold", workflow="w", step_index=1
    )

    restored = pipe.load([env0, env1])
    assert isinstance(restored, dict)
    assert restored["count"] == 1
    assert PLACEHOLDER_RE.fullmatch(str(restored["secret"]))


def test_load_empty_sequence_raises() -> None:
    pipe = make_pipeline()
    with pytest.raises(ChowkiStateError, match="empty"):
        pipe.load([])


def test_load_without_keyring_on_encrypted_envelope_raises() -> None:
    keyring = KeyRing.from_key(b"k" * 32, key_id="k1")
    pipe_enc = make_pipeline(keyring=keyring)
    env = pipe_enc.snapshot({"a": 1}, run_id="r", workflow="w", step_index=0)

    pipe_no_key = make_pipeline()
    with pytest.raises(DecryptionError):
        pipe_no_key.load([env])


def test_load_tampered_payload_raises_integrity_error() -> None:
    import msgspec

    pipe = make_pipeline()
    env = pipe.snapshot({"a": 1}, run_id="r", workflow="w", step_index=0)
    tampered = msgspec.structs.replace(env, payload=b"\x81\xa1a\x02")
    with pytest.raises(SnapshotIntegrityError):
        pipe.load([tampered])

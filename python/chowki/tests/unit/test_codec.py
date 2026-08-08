from __future__ import annotations

import json
from typing import Any, cast

import pytest

from chowki.errors import SchemaVersionError
from chowki.state.codec import (
    MIGRATIONS,
    decode_state,
    encode_state,
    migrate,
    register_migration,
    seal,
    unseal,
)
from chowki.types import SCHEMA_VERSION, SnapshotEnvelope, SnapshotKind


def test_msgpack_roundtrip_preserves_value() -> None:
    state = {"messages": [{"role": "user", "content": "hi"}], "n": 3, "f": 1.5, "b": True}
    assert decode_state(encode_state(state)) == state


def test_encode_state_rejects_set() -> None:
    with pytest.raises(TypeError, match="set is not JSON serializable"):
        encode_state(cast(Any, {1, 2, 3}))


def test_msgpack_is_smaller_than_json() -> None:
    """ADR-002 claims 30-50% smaller; assert the direction, not the exact ratio."""
    state = {"messages": [{"role": "user", "content": "hello world"}] * 200}
    assert len(encode_state(state)) < len(json.dumps(state).encode()) * 0.8


def test_seal_produces_a_versioned_envelope_with_a_matching_hash() -> None:
    env = seal(
        {"a": 1},
        run_id="run_1",
        workflow="demo",
        tenant_id="t1",
        step_index=0,
        kind=SnapshotKind.BASE,
    )
    assert env.v == SCHEMA_VERSION
    assert env.codec == "msgpack"
    assert env.state_hash.startswith("sha256:")
    assert unseal(env) == {"a": 1}


def test_unseal_detects_a_tampered_payload() -> None:
    from chowki.errors import SnapshotIntegrityError

    env = seal(
        {"a": 1}, run_id="r", workflow="w", tenant_id="t", step_index=0, kind=SnapshotKind.BASE
    )
    tampered = msgspec_replace(env, payload=encode_state({"a": 2}))
    with pytest.raises(SnapshotIntegrityError):
        unseal(tampered)


def test_unseal_rejects_a_future_schema_version() -> None:
    env = seal(
        {"a": 1}, run_id="r", workflow="w", tenant_id="t", step_index=0, kind=SnapshotKind.BASE
    )
    future = msgspec_replace(env, v=SCHEMA_VERSION + 5)
    with pytest.raises(SchemaVersionError, match="newer"):
        unseal(future)


def test_migration_chain_runs_in_order() -> None:
    calls: list[int] = []

    @register_migration(from_version=90)
    def _v90(payload: dict[str, object]) -> dict[str, object]:
        calls.append(90)
        payload["memory"] = {"short_term": payload.pop("mem", {})}
        return payload

    @register_migration(from_version=91)
    def _v91(payload: dict[str, object]) -> dict[str, object]:
        calls.append(91)
        payload["memory"]["v"] = 92  # type: ignore[index]
        return payload

    _ = (_v90, _v91)

    try:
        out = migrate({"mem": {"goal": "x"}}, from_version=90, to_version=92)
        assert calls == [90, 91]
        assert out == {"memory": {"short_term": {"goal": "x"}, "v": 92}}
    finally:
        MIGRATIONS.pop(90, None)
        MIGRATIONS.pop(91, None)


def test_migration_gap_is_a_hard_error() -> None:
    with pytest.raises(SchemaVersionError, match="no migration"):
        migrate({"a": 1}, from_version=70, to_version=72)


def test_registering_a_duplicate_migration_is_rejected() -> None:
    @register_migration(from_version=95)
    def _first(payload: dict[str, object]) -> dict[str, object]:
        return payload

    _ = _first

    try:
        with pytest.raises(ValueError, match="already registered"):
            register_migration(from_version=95)(lambda p: p)
    finally:
        MIGRATIONS.pop(95, None)


def msgspec_replace(obj: SnapshotEnvelope, **changes: object) -> SnapshotEnvelope:
    import msgspec

    return msgspec.structs.replace(obj, **changes)

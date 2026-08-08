"""MessagePack state codec, snapshot envelope sealing, and schema migrations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final, TypeVar, cast

import msgspec

from chowki.errors import SchemaVersionError, SnapshotIntegrityError
from chowki.state.canonical import hash_bytes
from chowki.types import SCHEMA_VERSION, JSONValue, SnapshotEnvelope, SnapshotKind

_ENCODER: Final = msgspec.msgpack.Encoder()
_DECODER: Final = msgspec.msgpack.Decoder()

T = TypeVar("T", bound=msgspec.Struct)


def encode_state(value: JSONValue) -> bytes:
    """Encode state value to MessagePack bytes."""
    if isinstance(value, (set, frozenset)):
        raise TypeError("Object of type set is not JSON serializable")
    return _ENCODER.encode(value)


def decode_state(raw: bytes) -> JSONValue:
    """Decode MessagePack bytes to state value."""
    return cast(JSONValue, _DECODER.decode(raw))


def encode_struct(obj: msgspec.Struct) -> bytes:
    """Encode msgspec Struct to MessagePack bytes."""
    return _ENCODER.encode(obj)


def decode_struct(raw: bytes, type_: type[T]) -> T:
    """Decode MessagePack bytes to a specific msgspec Struct type."""
    return msgspec.msgpack.decode(raw, type=type_)


def seal(
    state: JSONValue,
    *,
    run_id: str,
    workflow: str,
    tenant_id: str,
    step_index: int,
    kind: SnapshotKind,
    parent_hash: str | None = None,
) -> SnapshotEnvelope:
    """Seal state into a versioned SnapshotEnvelope with matching hash."""
    payload = encode_state(state)
    state_hash = hash_bytes(payload)
    created_at_utc = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    return SnapshotEnvelope(
        v=SCHEMA_VERSION,
        run_id=run_id,
        workflow=workflow,
        tenant_id=tenant_id,
        step_index=step_index,
        kind=kind,
        created_at_utc=created_at_utc,
        state_hash=state_hash,
        payload=payload,
        parent_hash=parent_hash,
        codec="msgpack",
    )


def unseal(env: SnapshotEnvelope) -> JSONValue:
    """Unseal SnapshotEnvelope, verifying version, integrity hash, and running migrations."""
    if env.v > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"snapshot schema v{env.v} is newer than this chowki build "
            f"(v{SCHEMA_VERSION}); upgrade chowki"
        )

    computed_hash = hash_bytes(env.payload)
    if computed_hash != env.state_hash:
        raise SnapshotIntegrityError(
            f"state_hash mismatch: envelope has {env.state_hash}, payload hash is {computed_hash}"
        )

    state = decode_state(env.payload)

    if env.v < SCHEMA_VERSION:
        if not isinstance(state, dict):
            raise SchemaVersionError(
                f"cannot migrate non-dict state snapshot from v{env.v} to v{SCHEMA_VERSION}"
            )
        state = migrate(state, from_version=env.v, to_version=SCHEMA_VERSION)

    return state


Migration = Callable[[dict[str, Any]], dict[str, Any]]
MIGRATIONS: Final[dict[int, Migration]] = {}


def register_migration(*, from_version: int) -> Callable[[Migration], Migration]:
    """Decorator to register a schema migration function from from_version to from_version + 1."""

    def decorator(fn: Migration) -> Migration:
        if from_version in MIGRATIONS:
            raise ValueError(f"migration from v{from_version} already registered")
        MIGRATIONS[from_version] = fn
        return fn

    return decorator


def migrate(payload: dict[str, Any], *, from_version: int, to_version: int) -> dict[str, Any]:
    """Migrate state payload sequentially from from_version to to_version."""
    current = from_version
    result = dict(payload)
    while current < to_version:
        fn = MIGRATIONS.get(current)
        if fn is None:
            raise SchemaVersionError(
                f"no migration registered from schema v{current}; cannot reach v{to_version}"
            )
        result = fn(result)
        current += 1
    return result

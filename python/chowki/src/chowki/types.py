"""Core chowki wire types. Field order is part of the on-disk format — never reorder."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

import msgspec

SCHEMA_VERSION: Final = 1

JSONValue = None | bool | int | float | str | list[Any] | dict[str, Any]
JSONObject = dict[str, JSONValue]


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    REJECTED = "REJECTED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SnapshotKind(StrEnum):
    BASE = "base"
    DELTA = "delta"


class Decision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    EDIT = "EDIT"
    ESCALATE = "ESCALATE"


class Usage(msgspec.Struct, kw_only=True, frozen=True):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def billable_tokens(self) -> int:
        """Cached input tokens are excluded: they are discounted, not free of charge,
        and are tracked separately for cost, not for the token ceiling
        (docs/research/04-guardrails.md:62-68)."""
        return self.input_tokens + self.output_tokens + self.reasoning_tokens

    def merge(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
        )


class SnapshotEnvelope(msgspec.Struct, kw_only=True, frozen=True):
    """Versioned header wrapping every persisted payload
    (docs/research/02-serialization.md:99-118)."""

    v: int
    run_id: str
    workflow: str
    tenant_id: str
    step_index: int
    kind: SnapshotKind
    created_at_utc: str
    state_hash: str
    payload: bytes
    parent_hash: str | None = None
    key_id: str | None = None
    nonce: bytes | None = None
    codec: str = "msgpack"

    def aad(self) -> bytes:
        """Associated authenticated data binding tenant, run, and schema version
        (ADR-003 / docs/research/02-serialization.md:236-256)."""
        return f"{self.tenant_id}:{self.run_id}:v{self.v}".encode()


class StepError(msgspec.Struct, kw_only=True, frozen=True):
    error_class: str
    message: str
    traceback: str | None = None


class StepRecord(msgspec.Struct, kw_only=True):
    run_id: str
    step_id: str
    name: str
    ordinal: int
    idempotency_key: str
    args_hash: str
    started_at_utc: str
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    result: bytes | None = None
    error: StepError | None = None
    ended_at_utc: str | None = None


def _empty_json_object() -> JSONObject:
    return {}


class PauseRequest(msgspec.Struct, kw_only=True, frozen=True):
    step_id: str
    reason: str
    permitted_actions: tuple[str, ...] = ("APPROVE", "REJECT")
    payload: JSONObject = msgspec.field(default_factory=_empty_json_object)
    reviewers: tuple[str, ...] = ()
    channel: str = "console"
    created_at_utc: str = ""


class RunRecord(msgspec.Struct, kw_only=True):
    run_id: str
    workflow: str
    tenant_id: str
    created_at_utc: str
    updated_at_utc: str
    status: RunStatus = RunStatus.PENDING
    schema_version: int = SCHEMA_VERSION
    step_cursor: int = 0
    pause: PauseRequest | None = None
    usage: Usage = msgspec.field(default_factory=Usage)

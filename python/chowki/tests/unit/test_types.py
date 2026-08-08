# python/chowki/tests/unit/test_types.py
from __future__ import annotations

import msgspec
import pytest

from chowki.types import (
    SCHEMA_VERSION,
    PauseRequest,
    RunRecord,
    RunStatus,
    SnapshotEnvelope,
    SnapshotKind,
    StepRecord,
    StepStatus,
    Usage,
)


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


def test_envelope_roundtrips_through_msgpack() -> None:
    env = SnapshotEnvelope(
        v=SCHEMA_VERSION,
        run_id="run_01",
        workflow="demo",
        tenant_id="t1",
        step_index=3,
        kind=SnapshotKind.BASE,
        created_at_utc="2026-08-08T06:00:00Z",
        state_hash="sha256:" + "0" * 64,
        payload=b"\x81\xa1a\x01",
    )
    raw = msgspec.msgpack.encode(env)
    back = msgspec.msgpack.decode(raw, type=SnapshotEnvelope)
    assert back == env
    assert back.parent_hash is None
    assert back.key_id is None


def test_envelope_is_frozen() -> None:
    env = SnapshotEnvelope(
        v=SCHEMA_VERSION,
        run_id="r",
        workflow="w",
        tenant_id="t",
        step_index=0,
        kind=SnapshotKind.BASE,
        created_at_utc="2026-08-08T06:00:00Z",
        state_hash="sha256:" + "0" * 64,
        payload=b"",
    )
    with pytest.raises(AttributeError):
        env.step_index = 9  # type: ignore[misc]


def test_envelope_field_order_is_pinned() -> None:
    """Wire compatibility: reordering fields silently breaks stored snapshots."""
    assert [f.name for f in msgspec.structs.fields(SnapshotEnvelope)] == [
        "v",
        "run_id",
        "workflow",
        "tenant_id",
        "step_index",
        "kind",
        "created_at_utc",
        "state_hash",
        "payload",
        "parent_hash",
        "key_id",
        "nonce",
        "codec",
    ]


def test_usage_accumulates() -> None:
    a = Usage(input_tokens=10, output_tokens=5, cost_usd=0.01)
    b = Usage(input_tokens=1, reasoning_tokens=7, cost_usd=0.02)
    total = a.merge(b)
    assert total.input_tokens == 11
    assert total.output_tokens == 5
    assert total.reasoning_tokens == 7
    assert total.cost_usd == pytest.approx(0.03)
    assert total.billable_tokens == 11 + 5 + 7


def test_step_record_defaults() -> None:
    rec = StepRecord(
        run_id="r",
        step_id="fetch#0",
        name="fetch",
        ordinal=0,
        idempotency_key="k",
        args_hash="sha256:" + "1" * 64,
        started_at_utc="2026-08-08T06:00:00Z",
    )
    assert rec.status is StepStatus.PENDING
    assert rec.attempts == 0
    assert rec.result is None


def test_run_record_and_pause_roundtrip() -> None:
    run = RunRecord(
        run_id="r",
        workflow="w",
        tenant_id="t",
        created_at_utc="2026-08-08T06:00:00Z",
        updated_at_utc="2026-08-08T06:00:00Z",
        pause=PauseRequest(
            step_id="approve#0",
            reason="human approval",
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
            payload={"amount": 5000},
        ),
    )
    assert run.status is RunStatus.PENDING
    raw = msgspec.msgpack.encode(run)
    back = msgspec.msgpack.decode(raw, type=RunRecord)
    assert back.pause is not None
    assert back.pause.permitted_actions == ("APPROVE", "REJECT", "EDIT")

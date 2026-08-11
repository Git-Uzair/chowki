from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from chowki.telemetry.logging import configure_logging
from chowki.telemetry.tracing import record_snapshot_metrics

if TYPE_CHECKING:
    import pytest


def test_production_logging_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(environment="production")
    structlog.get_logger().info("chowki_test_event", run_id="r1")
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["event"] == "chowki_test_event"
    assert payload["run_id"] == "r1"
    assert "timestamp" in payload
    assert payload.get("logger") == "chowki"
    assert payload.get("level") == "info"


def test_metrics_are_a_no_op_without_the_otel_sdk() -> None:
    """opentelemetry-api alone must not raise; the SDK is an optional extra."""
    record_snapshot_metrics(step="s", byte_size=1234, status="success")

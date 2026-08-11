from __future__ import annotations

import json

import pytest
import structlog

import chowki
from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.storage.memory import MemoryStorage
from chowki.telemetry import tracing
from chowki.telemetry.logging import configure_logging
from chowki.telemetry.tracing import record_snapshot_metrics


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


async def test_tracing_enabled_emits_step_spans_and_snapshot_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tracing_enabled=True` must actually reach OTel, on both the sync and async paths."""
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("chowki.test")

    # The tracer and the instruments are bound at import, so the SDK ones are swapped in
    # here rather than installed with set_tracer_provider/set_meter_provider: those are
    # process-wide and one-shot, and would leak chowki's telemetry into every other test.
    monkeypatch.setattr(tracing, "tracer", tracer_provider.get_tracer("chowki.test"))
    monkeypatch.setattr(tracing, "step_counter", meter.create_counter("chowki.step.count"))
    monkeypatch.setattr(
        tracing, "state_save_counter", meter.create_counter("chowki.state.save.count")
    )
    monkeypatch.setattr(
        tracing, "state_bytes_histogram", meter.create_histogram("chowki.state.size.bytes")
    )

    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), tracing_enabled=True))

    @chowki.step
    def summarise() -> str:
        return "summary"

    @chowki.step
    async def translate() -> str:
        return "translation"

    @chowki.workflow(engine=engine)
    async def observed_workflow() -> str:
        return f"{summarise()} {await translate()}"

    try:
        assert await observed_workflow(run_id="run_traced") == "summary translation"
    finally:
        engine.close()

    span_names = [span.name for span in exporter.get_finished_spans()]
    assert "chowki.step.summarise" in span_names, "the sync step path must open a span"
    assert "chowki.step.translate" in span_names, "the async step path must open a span"

    metrics_data = reader.get_metrics_data()
    assert metrics_data is not None
    metric_names = {
        metric.name
        for resource in metrics_data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert "chowki.state.save.count" in metric_names, "snapshot dispatch must count the save"
    assert "chowki.state.size.bytes" in metric_names, "snapshot dispatch must record the size"

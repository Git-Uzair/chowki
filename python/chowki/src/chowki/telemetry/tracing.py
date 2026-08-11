"""OpenTelemetry tracing and metrics integration for chowki."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import metrics, trace

tracer = trace.get_tracer("chowki.sdk", "0.1.0")
meter = metrics.get_meter("chowki.sdk", "0.1.0")

state_save_counter = meter.create_counter(
    "chowki.state.save.count", description="Total state capture operations", unit="1"
)
state_bytes_histogram = meter.create_histogram(
    "chowki.state.size.bytes", description="Serialized state snapshot payload size", unit="By"
)
step_counter = meter.create_counter(
    "chowki.step.count", description="Total step executions", unit="1"
)
budget_warning_counter = meter.create_counter(
    "chowki.budget.warning.count", description="Total budget warning events", unit="1"
)
loop_detected_counter = meter.create_counter(
    "chowki.loop.detected.count", description="Total loop detection events", unit="1"
)


@contextmanager
def span_for_step(name: str, enabled: bool = True) -> Generator[trace.Span | None, None, None]:
    """Context manager creating an OpenTelemetry span for a step execution."""
    if not enabled:
        yield None
        return

    with tracer.start_as_current_span(f"chowki.step.{name}") as span:
        span.set_attribute("chowki.step_name", name)
        try:
            yield span
            span.set_status(trace.Status(trace.StatusCode.OK))
            step_counter.add(1, {"step": name, "status": "success"})
        except Exception as exc:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            step_counter.add(1, {"step": name, "status": "error"})
            raise


def record_snapshot_metrics(*, step: str, byte_size: int, status: str = "success") -> None:
    """Record metrics for snapshot pipeline persistence."""
    state_save_counter.add(1, {"step": step, "status": status})
    state_bytes_histogram.record(byte_size, {"step": step, "status": status})

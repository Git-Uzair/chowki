from __future__ import annotations

import json
from pathlib import Path

import msgspec
import pytest

import chowki
from chowki.config import ChowkiEngine
from chowki.types import SnapshotEnvelope


def test_public_surface_is_exactly_as_documented() -> None:
    assert set(chowki.__all__) == {
        "__version__",
        "BudgetExceeded",
        "ChowkiConfig",
        "ChowkiError",
        "Decision",
        "GuardrailConfig",
        "HumanRejectedError",
        "InfiniteLoopDetected",
        "PauseRequest",
        "RunStatus",
        "StepStatus",
        "Usage",
        "WorkflowPaused",
        "complete_step",
        "configure",
        "current_run",
        "pause",
        "record_text",
        "record_transition",
        "recover_runs",
        "reissue_token",
        "release_step",
        "report_usage",
        "rerun",
        "resumable_runs",
        "resume",
        "step",
        "workflow",
    }


def test_every_exported_name_resolves() -> None:
    for name in chowki.__all__:
        assert getattr(chowki, name) is not None


def test_no_banned_product_term_in_the_package() -> None:
    banned = "check" + "point"
    root = Path(chowki.__file__).parent
    offenders = [p for p in root.rglob("*.py") if banned in p.read_text(encoding="utf-8").lower()]
    assert offenders == []


def test_importing_chowki_does_not_touch_the_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare import must not create .chowki/ — the engine is lazy."""
    import subprocess
    import sys

    monkeypatch.chdir(tmp_path)
    subprocess.run([sys.executable, "-c", "import chowki"], check=True)
    assert not (tmp_path / ".chowki").exists()


def test_snapshot_envelope_schema_required_fields_match_struct() -> None:
    schema_path = Path(__file__).parents[4] / "spec" / "v1" / "snapshot-envelope.schema.json"
    assert schema_path.exists(), f"Schema file missing at {schema_path}"

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    struct_required = [f.name for f in msgspec.structs.fields(SnapshotEnvelope) if f.required]
    assert schema["required"] == struct_required


def test_report_usage_persists_in_run_record() -> None:
    from chowki.config import ChowkiConfig, ChowkiEngine
    from chowki.storage.memory import MemoryStorage

    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))

    @chowki.workflow(engine=engine)
    def my_workflow() -> str:
        chowki.report_usage(chowki.Usage(input_tokens=1200, output_tokens=300))
        return "done"

    my_workflow(run_id="run_usage_test")
    record = engine.storage.get_run("run_usage_test")
    assert record is not None
    assert record.usage.input_tokens == 1200
    assert record.usage.output_tokens == 300
    assert record.usage.billable_tokens == 1500

    @chowki.workflow(engine=engine)
    def my_int_workflow() -> str:
        chowki.report_usage(500)
        return "done"

    my_int_workflow(run_id="run_int_usage_test")
    record_int = engine.storage.get_run("run_int_usage_test")
    assert record_int is not None
    assert record_int.usage.input_tokens == 500
    assert record_int.usage.billable_tokens == 500


def test_report_usage_persists_across_the_paused_boundary(engine: ChowkiEngine) -> None:
    """A suspension is a billing boundary: what was spent before it must be durable.

    Two separate writes are asserted, because a paused run is persisted twice: `pause()`
    itself, whose write is the only one a process that dies inside the suspension leaves
    behind, and the wrapper's close, which sees whatever the body spent after catching
    `WorkflowPaused`. Asserting only the final record would let either write disappear.
    """
    at_pause: list[int] = []

    @chowki.workflow(engine=engine)
    def approve_spend() -> str:
        chowki.report_usage(chowki.Usage(input_tokens=800, output_tokens=200))
        try:
            chowki.pause(reason="approve spend")
        except chowki.WorkflowPaused:
            suspended = engine.storage.get_run("run_paused_usage")
            assert suspended is not None
            at_pause.append(suspended.usage.billable_tokens)
            chowki.report_usage(chowki.Usage(output_tokens=100))
            raise
        return "done"

    with pytest.raises(chowki.WorkflowPaused):
        approve_spend(run_id="run_paused_usage")

    assert at_pause == [1000], "pause() must persist the usage spent up to the suspension"

    record = engine.storage.get_run("run_paused_usage")
    assert record is not None
    assert record.status is chowki.RunStatus.PAUSED
    assert record.usage.billable_tokens == 1100, "the PAUSED close must keep the run's usage"


def test_resumed_run_accumulates_usage_across_the_pause(engine: ChowkiEngine) -> None:
    """Usage is cumulative per run, so a resume continues the tally instead of restarting it.

    The reports live inside steps, so the replay of the body before the gate does not
    re-bill them: the pre-pause total has to come from the stored record, and the budget
    the resumed execution enforces has to start from it too.
    """
    seen_usage: list[int] = []
    seen_budget: list[int] = []

    @chowki.step
    def draft() -> str:
        chowki.report_usage(chowki.Usage(input_tokens=800, output_tokens=200))
        return "draft"

    @chowki.step
    def deliver() -> str:
        chowki.report_usage(chowki.Usage(input_tokens=300))
        return "delivered"

    @chowki.workflow(engine=engine)
    def spend_around_a_gate() -> str:
        ctx = chowki.current_run()
        seen_usage.append(ctx.usage.billable_tokens)
        seen_budget.append(ctx.budget.total.billable_tokens)
        draft()
        chowki.pause(reason="approve spend")
        return deliver()

    with pytest.raises(chowki.WorkflowPaused) as excinfo:
        spend_around_a_gate(run_id="run_resume_usage")
    token = excinfo.value.token
    assert token is not None

    paused = engine.storage.get_run("run_resume_usage")
    assert paused is not None
    assert paused.usage.billable_tokens == 1000

    chowki.resume(
        run_id="run_resume_usage",
        token=token,
        decision=chowki.Decision.APPROVE,
        workflow_fn=spend_around_a_gate,
        engine=engine,
    )

    assert seen_usage == [0, 1000], "the resumed context must open with the run's stored usage"
    assert seen_budget == [0, 1000], "the resumed budget must be charged for the pre-pause spend"

    record = engine.storage.get_run("run_resume_usage")
    assert record is not None
    assert record.status is chowki.RunStatus.COMPLETED
    assert record.usage.billable_tokens == 1300

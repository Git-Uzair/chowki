from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec

import chowki
from chowki.types import SnapshotEnvelope

if TYPE_CHECKING:
    import pytest


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
        "configure",
        "current_run",
        "pause",
        "recover_runs",
        "report_usage",
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

from __future__ import annotations

from chowki.config import ChowkiEngine
from chowki.core.runner import recover_runs, resumable_runs
from chowki.types import RunStatus


def _seed(engine: ChowkiEngine, run_id: str, status: RunStatus) -> None:
    from chowki.types import RunRecord

    engine.storage.put_run(
        RunRecord(
            run_id=run_id,
            workflow="w",
            tenant_id="default",
            created_at_utc="2026-08-08T06:00:00Z",
            updated_at_utc="2026-08-08T06:00:00Z",
            status=status,
        )
    )


def test_resumable_runs_lists_only_incomplete_runs(engine: ChowkiEngine) -> None:
    _seed(engine, "a", RunStatus.RUNNING)
    _seed(engine, "b", RunStatus.PAUSED)
    _seed(engine, "c", RunStatus.PENDING)
    _seed(engine, "d", RunStatus.COMPLETED)
    _seed(engine, "e", RunStatus.FAILED)
    _seed(engine, "f", RunStatus.ABORTED)

    assert sorted(r.run_id for r in resumable_runs(engine)) == ["a", "b", "c"]


def test_recover_runs_reports_but_does_not_execute(engine: ChowkiEngine) -> None:
    """Recovery must never auto-run side effects; it hands the caller a list."""
    _seed(engine, "a", RunStatus.RUNNING)
    found = recover_runs(engine)
    assert [r.run_id for r in found] == ["a"]
    run = engine.storage.get_run("a")
    assert run is not None and run.status is RunStatus.PENDING  # re-armed, not executed


def test_recover_runs_ignores_failed_runs(engine: ChowkiEngine) -> None:
    _seed(engine, "r_running", RunStatus.RUNNING)
    _seed(engine, "r_paused", RunStatus.PAUSED)
    _seed(engine, "r_pending", RunStatus.PENDING)
    _seed(engine, "r_failed", RunStatus.FAILED)
    _seed(engine, "r_completed", RunStatus.COMPLETED)

    found = recover_runs(engine)
    found_ids = sorted(r.run_id for r in found)
    assert found_ids == ["r_paused", "r_pending", "r_running"]

    # FAILED run was untouched
    failed_run = engine.storage.get_run("r_failed")
    assert failed_run is not None and failed_run.status is RunStatus.FAILED

    # RUNNING run was flipped to PENDING
    running_run = engine.storage.get_run("r_running")
    assert running_run is not None and running_run.status is RunStatus.PENDING

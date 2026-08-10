# python/chowki/tests/unit/test_pause.py
from __future__ import annotations

import contextlib

import pytest

from chowki.config import ChowkiEngine
from chowki.core.context import current_run
from chowki.core.decorators import step, workflow
from chowki.core.runner import pause
from chowki.errors import WorkflowPaused
from chowki.types import RunStatus


def test_pause_suspends_the_run_and_persists_the_request(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def pipeline() -> None:
        pause(
            reason="approve the transfer",
            payload={"amount": 5000},
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
        )
        raise AssertionError("must not be reached")

    with pytest.raises(WorkflowPaused) as excinfo:
        pipeline(run_id="r1")

    assert excinfo.value.run_id == "r1"
    assert excinfo.value.token

    run = engine.storage.get_run("r1")
    assert run is not None
    assert run.status is RunStatus.PAUSED
    assert run.pause is not None
    assert run.pause.payload == {"amount": 5000}
    assert run.pause.permitted_actions == ("APPROVE", "REJECT", "EDIT")


def test_pause_snapshots_state_before_suspending(engine: ChowkiEngine) -> None:
    """The durable state of a paused run is the state as of the pause() call.

    A workflow that catches WorkflowPaused and keeps mutating state must not be able to
    overwrite the snapshot a resume will load.
    """

    @workflow(engine=engine)
    def pipeline() -> None:
        ctx = current_run()
        ctx.state["draft"] = "ready"
        try:
            pause(reason="review")
        except WorkflowPaused:
            ctx.state["draft"] = "after"

    pipeline(run_id="r2")

    snaps = engine.storage.snapshots_for_resume("r2")
    assert snaps
    assert engine.pipeline_for("r2").load(snaps) == {"draft": "ready"}


def test_pause_redacts_the_payload(engine: ChowkiEngine) -> None:
    secret = "sk-" + "A1b2C3d4E5f6G7h8I9j0"

    @workflow(engine=engine)
    def pipeline() -> None:
        pause(reason="review", payload={"token": secret})

    with pytest.raises(WorkflowPaused):
        pipeline(run_id="r3")

    run = engine.storage.get_run("r3")
    assert run is not None and run.pause is not None
    assert secret not in str(run.pause.payload)


def test_pause_persists_the_run_even_if_the_workflow_swallows_the_exception(
    engine: ChowkiEngine,
) -> None:
    """A workflow that catches WorkflowPaused must not leave the run marked RUNNING."""

    @workflow(engine=engine)
    def pipeline() -> str:
        try:
            pause(reason="review", payload={"amount": 1})
        except WorkflowPaused:
            return "swallowed"
        raise AssertionError("must not be reached")

    assert pipeline(run_id="r4") == "swallowed"

    run = engine.storage.get_run("r4")
    assert run is not None
    assert run.status is RunStatus.PAUSED
    assert run.pause is not None
    assert run.pause.reason == "review"
    assert run.pause.payload == {"amount": 1}


def test_a_step_called_after_a_pause_is_refused(engine: ChowkiEngine) -> None:
    """Once a run is paused no step may run: it would snapshot over the pause boundary.

    snapshots_for_resume replays every snapshot after the last base, so a step that
    succeeds after pause() writes a later snapshot that wins over the pause-time one.
    """
    calls: list[str] = []

    @step
    def clobber() -> str:
        calls.append("ran")
        current_run().state["draft"] = "clobbered"
        return "done"

    @workflow(engine=engine)
    def pipeline() -> None:
        ctx = current_run()
        ctx.state["draft"] = "ready"
        with contextlib.suppress(WorkflowPaused):
            pause(reason="review")
        clobber()

    with pytest.raises(WorkflowPaused) as excinfo:
        pipeline(run_id="r5")

    assert excinfo.value.run_id == "r5"
    assert calls == []
    assert engine.storage.list_steps("r5") == []

    run = engine.storage.get_run("r5")
    assert run is not None
    assert run.status is RunStatus.PAUSED

    snaps = engine.storage.snapshots_for_resume("r5")
    assert snaps
    assert engine.pipeline_for("r5").load(snaps) == {"draft": "ready"}


def test_pause_outside_a_workflow_is_an_error() -> None:
    with pytest.raises(LookupError):
        pause(reason="nope")

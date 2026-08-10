from __future__ import annotations

import pytest

from chowki.config import ChowkiEngine
from chowki.core.context import current_run
from chowki.core.decorators import step, workflow
from chowki.errors import ChowkiConfigError, HumanRejectedError, ToolExecutionError, WorkflowPaused
from chowki.types import PauseRequest, RunStatus


def test_workflow_creates_a_run_and_completes_it(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def pipeline(x: int) -> int:
        return x * 2

    assert pipeline(4, run_id="r1") == 8
    run = engine.storage.get_run("r1")
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert run.workflow == "pipeline"


def test_run_id_is_generated_when_not_supplied(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def pipeline() -> str:
        return current_run().run_id

    generated = pipeline()
    assert generated.startswith("run_")
    assert engine.storage.get_run(generated) is not None


def test_state_is_exposed_and_persisted(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def pipeline() -> None:
        current_run().state["goal"] = "optimize"

    pipeline(run_id="r2")
    assert len(engine.storage.list_snapshots("r2")) >= 1


def test_failure_marks_the_run_failed_and_propagates(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def pipeline() -> None:
        raise ToolExecutionError("boom")

    with pytest.raises(ToolExecutionError):
        pipeline(run_id="r3")

    run = engine.storage.get_run("r3")
    assert run is not None and run.status is RunStatus.FAILED


def test_steps_are_attributed_to_the_enclosing_run(engine: ChowkiEngine) -> None:
    @step
    def inner(x: int) -> int:
        return x + 1

    @workflow(engine=engine)
    def pipeline() -> int:
        return inner(inner(1))

    assert pipeline(run_id="r4") == 3
    assert [s.step_id for s in engine.storage.list_steps("r4")] == ["inner#0", "inner#1"]


def test_reusing_a_run_id_resumes_rather_than_restarting(engine: ChowkiEngine) -> None:
    calls: list[str] = []

    @step
    def once() -> str:
        calls.append("ran")
        return "value"

    @workflow(engine=engine)
    def pipeline() -> str:
        return once()

    assert pipeline(run_id="r5") == "value"
    assert pipeline(run_id="r5") == "value"
    assert calls == ["ran"]


def test_pipeline_is_dropped_when_the_run_terminates(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def pipeline() -> None:
        return None

    pipeline(run_id="r6")
    assert "r6" not in engine._pipelines  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_async_workflow(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    async def pipeline(x: int) -> int:
        return x + 1

    assert await pipeline(1, run_id="r7") == 2
    run = engine.storage.get_run("r7")
    assert run is not None and run.status is RunStatus.COMPLETED


def test_workflow_uses_the_process_engine_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chowki.config import configure, reset_engine
    from chowki.storage.memory import MemoryStorage

    reset_engine()
    store = MemoryStorage()
    configure(storage=store)

    @workflow
    def pipeline() -> int:
        return 1

    pipeline(run_id="r8")
    assert store.get_run("r8") is not None
    reset_engine()


def test_disallow_reserved_parameter_names() -> None:
    with pytest.raises(ChowkiConfigError, match="run_id"):

        @workflow
        def bad_run_id(run_id: str) -> None:
            pass

        _ = bad_run_id

    with pytest.raises(ChowkiConfigError, match="tenant_id"):

        @workflow
        def bad_tenant_id(tenant_id: str) -> None:
            pass

        _ = bad_tenant_id


def test_workflow_paused_and_rejected_handling(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def paused_wf() -> None:
        current_run().pause = PauseRequest(step_id="step1", reason="need review")
        raise WorkflowPaused("p1", "step1")

    with pytest.raises(WorkflowPaused):
        paused_wf(run_id="p1")

    run = engine.storage.get_run("p1")
    assert run is not None
    assert run.status is RunStatus.PAUSED
    assert run.pause is not None and run.pause.step_id == "step1"
    # Pipeline should not be dropped on PAUSED
    assert "p1" in engine._pipelines  # pyright: ignore[reportPrivateUsage]

    @workflow(engine=engine)
    def rejected_wf() -> None:
        raise HumanRejectedError("rej1", "step1")

    with pytest.raises(HumanRejectedError):
        rejected_wf(run_id="rej1")

    run_rej = engine.storage.get_run("rej1")
    assert run_rej is not None
    assert run_rej.status is RunStatus.REJECTED
    assert "rej1" not in engine._pipelines  # pyright: ignore[reportPrivateUsage]

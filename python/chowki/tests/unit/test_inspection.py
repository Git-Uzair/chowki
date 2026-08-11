from __future__ import annotations

import pytest

import chowki
from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import current_run
from chowki.core.decorators import step, workflow
from chowki.core.runner import pause
from chowki.errors import ChowkiStateError, WorkflowPaused
from chowki.state.crypto import KeyRing
from chowki.storage.memory import MemoryStorage
from chowki.types import Decision, RunStatus


def test_inspect_completed_run(engine: ChowkiEngine) -> None:
    @step
    def add_data() -> str:
        current_run().state["data"] = "hello world"
        return "step_result"

    @workflow(engine=engine)
    def my_workflow() -> str:
        return add_data()

    my_workflow(run_id="run_completed")

    inspection = chowki.inspect_run("run_completed", engine=engine)
    assert isinstance(inspection, chowki.RunInspection)
    assert inspection.run.run_id == "run_completed"
    assert inspection.run.status is RunStatus.COMPLETED
    assert inspection.resumable is False
    assert inspection.pause is None
    assert len(inspection.steps) == 1
    assert inspection.steps[0].step_id == "add_data#0"
    assert inspection.state == {"data": "hello world"}


def test_inspect_paused_run(engine: ChowkiEngine) -> None:
    @step
    def prep() -> None:
        current_run().state["status"] = "prepped"

    @workflow(engine=engine)
    def paused_wf() -> None:
        prep()
        pause(reason="need approval", payload={"key": "val"})

    with pytest.raises(WorkflowPaused):
        paused_wf(run_id="run_paused")

    inspection = chowki.inspect_run("run_paused", engine=engine)
    assert inspection.run.status is RunStatus.PAUSED
    assert inspection.resumable is True
    assert inspection.pause is not None
    assert inspection.pause.reason == "need approval"
    assert inspection.pause.payload == {"key": "val"}
    assert inspection.state == {"status": "prepped"}


def test_inspect_redaction(engine: ChowkiEngine) -> None:
    secret = "sk-proj-1234567890abcdef1234567890abcdef"

    @step
    def set_secret() -> None:
        current_run().state["api_key"] = secret

    @workflow(engine=engine)
    def secret_wf() -> None:
        set_secret()

    secret_wf(run_id="run_secret")

    inspection = chowki.inspect_run("run_secret", engine=engine)
    assert inspection.state is not None
    assert isinstance(inspection.state, dict)
    redacted_val = str(inspection.state.get("api_key"))
    assert secret not in redacted_val
    assert "[REDACTED:" in redacted_val


def test_inspect_unknown_run_raises(engine: ChowkiEngine) -> None:
    with pytest.raises(ChowkiStateError, match="unknown run_id"):
        chowki.inspect_run("does_not_exist", engine=engine)


def test_inspect_pipeline_isolation(engine: ChowkiEngine) -> None:
    @step
    def step_one() -> None:
        current_run().state["step1"] = "done"

    @step
    def step_two() -> None:
        current_run().state["step2"] = "done"

    @workflow(engine=engine)
    def isol_wf() -> str:
        step_one()
        pause(reason="gate 1")
        step_two()
        return "finished"

    with pytest.raises(WorkflowPaused) as excinfo:
        isol_wf(run_id="run_isol")

    token = excinfo.value.token
    assert token is not None

    # Inspect while paused
    inspection1 = chowki.inspect_run("run_isol", engine=engine)
    assert inspection1.run.status is RunStatus.PAUSED
    assert inspection1.state == {"step1": "done"}

    # Resume the workflow now - pipeline isolation ensures resume works cleanly
    res = chowki.resume(
        run_id="run_isol",
        token=token,
        decision=Decision.APPROVE,
        workflow_fn=isol_wf,
        engine=engine,
    )
    assert res.value == "finished"

    # Inspect completed
    inspection2 = chowki.inspect_run("run_isol", engine=engine)
    assert inspection2.run.status is RunStatus.COMPLETED
    assert inspection2.resumable is False
    assert inspection2.state == {"step1": "done", "step2": "done"}


def test_inspect_encrypted_at_rest_run() -> None:
    keyring = KeyRing({"k1": b"0" * 32}, active_key_id="k1")
    enc_engine = ChowkiEngine(
        ChowkiConfig(storage=MemoryStorage(), keyring=keyring, encrypt_at_rest=True)
    )

    @step
    def enc_step() -> None:
        current_run().state["enc"] = "secret_data"

    @workflow(engine=enc_engine)
    def enc_wf() -> None:
        enc_step()

    enc_wf(run_id="run_enc")

    inspection = chowki.inspect_run("run_enc", engine=enc_engine)
    assert inspection.run.status is RunStatus.COMPLETED
    assert inspection.state == {"enc": "secret_data"}

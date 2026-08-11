from __future__ import annotations

import re
from typing import Any, cast

import pytest

from chowki.config import ChowkiEngine
from chowki.core.context import current_run
from chowki.core.decorators import step, workflow
from chowki.core.resume import ResumeResult, aresume, resume
from chowki.core.runner import pause
from chowki.errors import ChowkiConfigError, HumanRejectedError, WorkflowPaused
from chowki.types import Decision, JSONObject, RunStatus


def build_async_workflow(engine: ChowkiEngine, calls: list[str]):
    @step
    def prepare() -> JSONObject:
        calls.append("prepare")
        return {"recipient": "wrong@example.com", "amount": 5000}

    @workflow(engine=engine)
    async def transfer() -> str:
        proposal = prepare()
        current_run().state["proposal"] = proposal
        pause(
            reason="approve transfer",
            payload=proposal,
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
        )
        calls.append("send")
        prop = cast(dict[str, Any], current_run().state["proposal"])
        return f"sent to {prop['recipient']}"

    return transfer


def build_two_gate_async_workflow(engine: ChowkiEngine, calls: list[str]):
    @step
    def step1() -> None:
        calls.append("gate1")

    @step
    def step2() -> None:
        calls.append("gate2")

    @workflow(engine=engine)
    async def multi_gate() -> str:
        step1()
        pause(reason="gate 1")
        step2()
        pause(reason="gate 2")
        calls.append("done")
        return "finished"

    return multi_gate


def build_sync_workflow(engine: ChowkiEngine, calls: list[str]):
    @step
    def step1() -> None:
        calls.append("gate1")

    @workflow(engine=engine)
    def sync_wf() -> str:
        step1()
        pause(reason="gate 1")
        calls.append("done")
        return "sync finished"

    return sync_wf


@pytest.mark.asyncio
async def test_aresume_approve_async_workflow_completes(engine: ChowkiEngine) -> None:
    calls: list[str] = []
    transfer = build_async_workflow(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo:
        await transfer(run_id="async_r1")
    token = excinfo.value.token
    assert token is not None

    result = await aresume(
        run_id="async_r1",
        token=token,
        decision=Decision.APPROVE,
        workflow_fn=transfer,
        engine=engine,
    )
    assert isinstance(result, ResumeResult)
    assert result.value == "sent to wrong@example.com"
    assert calls == ["prepare", "send"]

    run = engine.storage.get_run("async_r1")
    assert run is not None and run.status is RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_aresume_edit_patch_applies_to_async_workflow(engine: ChowkiEngine) -> None:
    calls: list[str] = []
    transfer = build_async_workflow(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo:
        await transfer(run_id="async_r2")
    token = excinfo.value.token
    assert token is not None

    result = await aresume(
        run_id="async_r2",
        token=token,
        decision=Decision.EDIT,
        patch=[{"op": "replace", "path": "/proposal/recipient", "value": "verified@company.com"}],
        workflow_fn=transfer,
        engine=engine,
    )
    assert result.value == "sent to verified@company.com"
    assert calls == ["prepare", "send"]


@pytest.mark.asyncio
async def test_aresume_reject_raises_and_marks_rejected(engine: ChowkiEngine) -> None:
    calls: list[str] = []
    transfer = build_async_workflow(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo:
        await transfer(run_id="async_r3")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(HumanRejectedError):
        await aresume(
            run_id="async_r3",
            token=token,
            decision=Decision.REJECT,
            workflow_fn=transfer,
            engine=engine,
        )

    run = engine.storage.get_run("async_r3")
    assert run is not None and run.status is RunStatus.REJECTED


@pytest.mark.asyncio
async def test_aresume_second_gate_raises_workflow_paused_with_fresh_token(
    engine: ChowkiEngine,
) -> None:
    calls: list[str] = []
    multi_gate = build_two_gate_async_workflow(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo1:
        await multi_gate(run_id="async_r4")
    token1 = excinfo1.value.token
    assert token1 is not None

    with pytest.raises(WorkflowPaused) as excinfo2:
        await aresume(
            run_id="async_r4",
            token=token1,
            decision=Decision.APPROVE,
            workflow_fn=multi_gate,
            engine=engine,
        )
    token2 = excinfo2.value.token
    assert token2 is not None
    assert token2 != token1

    result = await aresume(
        run_id="async_r4",
        token=token2,
        decision=Decision.APPROVE,
        workflow_fn=multi_gate,
        engine=engine,
    )
    assert result.value == "finished"
    assert calls == ["gate1", "gate2", "done"]


@pytest.mark.asyncio
async def test_sync_resume_on_async_workflow_raises_config_error_and_remains_paused(
    engine: ChowkiEngine,
) -> None:
    calls: list[str] = []
    transfer = build_async_workflow(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo:
        await transfer(run_id="async_r5")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(
        ChowkiConfigError, match=re.escape("async workflow: use chowki.aresume(...)")
    ):
        resume(
            run_id="async_r5",
            token=token,
            decision=Decision.APPROVE,
            workflow_fn=transfer,
            engine=engine,
        )

    run = engine.storage.get_run("async_r5")
    assert run is not None and run.status is RunStatus.PAUSED

    # Ensure it remains resumable via aresume with the same token
    result = await aresume(
        run_id="async_r5",
        token=token,
        decision=Decision.APPROVE,
        workflow_fn=transfer,
        engine=engine,
    )
    assert result.value == "sent to wrong@example.com"


@pytest.mark.asyncio
async def test_aresume_on_sync_workflow_works(engine: ChowkiEngine) -> None:
    calls: list[str] = []
    sync_wf = build_sync_workflow(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo:
        sync_wf(run_id="sync_r1")
    token = excinfo.value.token
    assert token is not None

    result = await aresume(
        run_id="sync_r1", token=token, decision=Decision.APPROVE, workflow_fn=sync_wf, engine=engine
    )
    assert isinstance(result, ResumeResult)
    assert result.value == "sync finished"
    assert calls == ["gate1", "done"]

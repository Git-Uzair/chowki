"""Guardrail breaches suspend the run for a human instead of failing it (ADR-005).

The breaker's PAUSE decision was previously advisory: the exception carried
`chowki_action` and the run ended FAILED with no token, so 'auto-pause' never
actually paused anything. These tests pin the wired behavior: a breaker PAUSE
becomes a durable PAUSED run with a resume token and a gateway notification,
raised to the caller as WorkflowPaused chained from the original error.
"""

from __future__ import annotations

import pytest

import chowki
from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import current_run
from chowki.errors import ToolExecutionError, WorkflowPaused
from chowki.guardrails.config import GuardrailConfig
from chowki.hitl.gateway import InMemoryGateway
from chowki.storage.memory import MemoryStorage
from chowki.types import Decision, RunStatus


def _engine(storage: MemoryStorage | None = None, **guardrails: object) -> ChowkiEngine:
    return ChowkiEngine(
        ChowkiConfig(
            storage=storage or MemoryStorage(),
            gateway=InMemoryGateway(),
            resume_secret=b"s" * 32,
            guardrails=GuardrailConfig(**guardrails),  # type: ignore[arg-type]
        )
    )


def test_a_retry_exhausted_step_auto_pauses_the_run() -> None:
    engine = _engine()
    calls: list[int] = []

    @chowki.step(retries=0)
    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise ToolExecutionError("downstream 500")
        return "ok"

    @chowki.workflow(engine=engine)
    def wf() -> str:
        current_run().state["r"] = flaky()
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        wf(run_id="ap1")
    assert isinstance(excinfo.value.__cause__, ToolExecutionError)
    token = excinfo.value.token
    assert token is not None

    run = engine.storage.get_run("ap1")
    assert run is not None and run.status is RunStatus.PAUSED
    assert run.pause is not None
    assert run.pause.origin == "auto"
    assert run.pause.step_id == "flaky#0"

    gateway = engine.gateway
    assert isinstance(gateway, InMemoryGateway) and len(gateway.notices) == 1

    result = chowki.resume(
        run_id="ap1", token=token, decision=Decision.APPROVE, workflow_fn=wf, engine=engine
    )
    assert result.value == "done"
    assert len(calls) == 2
    final = engine.storage.get_run("ap1")
    assert final is not None and final.status is RunStatus.COMPLETED
    engine.close()


@pytest.mark.asyncio
async def test_an_async_retry_exhausted_step_auto_pauses_the_run() -> None:
    engine = _engine()
    calls: list[int] = []

    @chowki.step(retries=0)
    async def flaky() -> str:
        calls.append(1)
        raise ToolExecutionError("downstream 500")

    @chowki.workflow(engine=engine)
    async def wf() -> str:
        current_run().state["r"] = await flaky()
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        await wf(run_id="ap_async")
    assert isinstance(excinfo.value.__cause__, ToolExecutionError)

    run = engine.storage.get_run("ap_async")
    assert run is not None and run.status is RunStatus.PAUSED
    assert run.pause is not None and run.pause.origin == "auto"
    engine.close()


def test_an_edit_decision_on_an_auto_pause_seeds_the_patched_state() -> None:
    """Gate pauses re-apply the human patch at the gate; an auto-pause has no gate
    in the body, so the re-execution must be seeded with the patched state."""
    engine = _engine()
    calls: list[int] = []

    @chowki.step(retries=0)
    def risky() -> str:
        calls.append(1)
        if current_run().state.get("mode") != "safe":
            raise ToolExecutionError("unsafe mode")
        return "ran-safely"

    @chowki.workflow(engine=engine)
    def wf() -> str:
        ctx = current_run()
        ctx.state.setdefault("mode", "fast")
        ctx.state["r"] = risky()
        return str(ctx.state["r"])

    with pytest.raises(WorkflowPaused) as excinfo:
        wf(run_id="ap2")
    token = excinfo.value.token
    assert token is not None

    result = chowki.resume(
        run_id="ap2",
        token=token,
        decision=Decision.EDIT,
        patch=[{"op": "replace", "path": "/mode", "value": "safe"}],
        workflow_fn=wf,
        engine=engine,
    )
    assert result.value == "ran-safely"
    assert len(calls) == 2
    engine.close()


def test_a_hard_budget_breach_in_the_body_auto_pauses() -> None:
    engine = _engine(max_token_budget=100)

    @chowki.workflow(engine=engine)
    def spendy() -> str:
        chowki.report_usage(chowki.Usage(input_tokens=150))
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        spendy(run_id="ap3")
    assert isinstance(excinfo.value.__cause__, chowki.BudgetExceeded)

    run = engine.storage.get_run("ap3")
    assert run is not None and run.status is RunStatus.PAUSED
    assert run.pause is not None and run.pause.origin == "auto"
    assert run.usage.billable_tokens == 150, "spend before the breach must be durable"
    engine.close()


def test_hard_budget_action_abort_fails_the_run_instead() -> None:
    engine = _engine(max_token_budget=100, hard_budget_action="ABORT")

    @chowki.workflow(engine=engine)
    def spendy() -> str:
        chowki.report_usage(chowki.Usage(input_tokens=150))
        return "done"

    with pytest.raises(chowki.BudgetExceeded):
        spendy(run_id="ap4")
    run = engine.storage.get_run("ap4")
    assert run is not None and run.status is RunStatus.FAILED
    engine.close()


def test_a_budget_pause_can_be_resumed_after_raising_the_budget() -> None:
    """The operator flow ADR-005 promises: hard breach pauses with warm state, a
    human raises the ceiling, and APPROVE continues from the boundary."""
    shared = MemoryStorage()

    def build(eng: ChowkiEngine) -> object:
        @chowki.step
        def think() -> str:
            chowki.report_usage(chowki.Usage(input_tokens=150))
            return "thought"

        @chowki.workflow(engine=eng, name="thinker")
        def thinker() -> str:
            current_run().state["t"] = think()
            return "done"

        return thinker

    small = ChowkiEngine(
        ChowkiConfig(
            storage=shared,
            resume_secret=b"s" * 32,
            guardrails=GuardrailConfig(max_token_budget=100),
        )
    )
    with pytest.raises(WorkflowPaused) as excinfo:
        build(small)(run_id="ap7")  # type: ignore[operator]
    token = excinfo.value.token
    assert token is not None

    paused = shared.get_run("ap7")
    assert paused is not None and paused.status is RunStatus.PAUSED

    big = ChowkiEngine(
        ChowkiConfig(
            storage=shared,
            resume_secret=b"s" * 32,
            guardrails=GuardrailConfig(max_token_budget=10_000),
        )
    )
    result = chowki.resume(
        run_id="ap7",
        token=token,
        decision=Decision.APPROVE,
        workflow_fn=build(big),  # type: ignore[arg-type]
        engine=big,
    )
    assert result.value == "done"
    final = shared.get_run("ap7")
    assert final is not None and final.status is RunStatus.COMPLETED
    assert final.usage.billable_tokens == 300, "the re-run's real spend accumulates"
    small.close()


def test_a_loop_guardrail_breach_auto_pauses() -> None:
    engine = _engine(max_steps_per_run=3)

    @chowki.step
    def tick(n: int) -> int:
        return n

    @chowki.workflow(engine=engine)
    def spinner() -> str:
        for i in range(10):
            tick(i)
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        spinner(run_id="ap5")
    assert isinstance(excinfo.value.__cause__, chowki.InfiniteLoopDetected)

    run = engine.storage.get_run("ap5")
    assert run is not None and run.status is RunStatus.PAUSED
    assert run.pause is not None and run.pause.origin == "auto"
    engine.close()


def test_a_plain_body_failure_still_fails_the_run() -> None:
    """The auto-pause boundary: only breaker PAUSE decisions and guardrail
    breaches suspend; an ordinary bug in the body stays a FAILED run."""
    engine = _engine()

    @chowki.workflow(engine=engine)
    def broken() -> None:
        raise ValueError("logic bug")

    with pytest.raises(ValueError, match="logic bug"):
        broken(run_id="ap6")
    run = engine.storage.get_run("ap6")
    assert run is not None and run.status is RunStatus.FAILED
    engine.close()


def test_an_auto_pause_without_a_gateway_still_pauses_with_a_token() -> None:
    """No gateway is not "no pause": the notification is optional, the suspension is not.

    Documented in the user guide -- an operator with `gateway=None` still gets a PAUSED
    run they can inspect and reissue a token for from the CLI.
    """
    engine = ChowkiEngine(
        ChowkiConfig(storage=MemoryStorage(), gateway=None, resume_secret=b"s" * 32)
    )

    @chowki.step(retries=0)
    def flaky() -> str:
        raise ToolExecutionError("downstream 500")

    @chowki.workflow(engine=engine)
    def wf() -> str:
        current_run().state["r"] = flaky()
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        wf(run_id="ap9")
    assert excinfo.value.token is not None

    run = engine.storage.get_run("ap9")
    assert run is not None and run.status is RunStatus.PAUSED
    assert run.pause is not None and run.pause.origin == "auto"
    assert chowki.reissue_token("ap9", engine=engine)
    engine.close()


def test_a_rejected_auto_pause_marks_the_run_rejected() -> None:
    engine = _engine()

    @chowki.step(retries=0)
    def flaky() -> str:
        raise ToolExecutionError("downstream 500")

    @chowki.workflow(engine=engine)
    def wf() -> str:
        current_run().state["r"] = flaky()
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        wf(run_id="ap8")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(chowki.HumanRejectedError):
        chowki.resume(
            run_id="ap8", token=token, decision=Decision.REJECT, workflow_fn=wf, engine=engine
        )
    run = engine.storage.get_run("ap8")
    assert run is not None and run.status is RunStatus.REJECTED
    engine.close()

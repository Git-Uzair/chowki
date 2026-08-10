# python/chowki/tests/unit/test_gateway.py
from __future__ import annotations

import pytest

from chowki.config import ChowkiEngine
from chowki.core.decorators import workflow
from chowki.core.runner import pause
from chowki.errors import WorkflowPaused
from chowki.hitl.console import ConsoleGateway
from chowki.hitl.gateway import ChannelGateway, GatewayHandle, InMemoryGateway, PauseNotice
from chowki.types import Decision


def test_in_memory_gateway_satisfies_the_protocol() -> None:
    assert isinstance(InMemoryGateway(), ChannelGateway)
    assert isinstance(ConsoleGateway(), ChannelGateway)


def test_notice_carries_everything_a_channel_needs_to_render() -> None:
    notice = PauseNotice(
        run_id="r1",
        workflow="transfer",
        step_id="approve#0",
        reason="approve the transfer",
        payload={"amount": 5000},
        permitted_actions=("APPROVE", "REJECT"),
        reviewers=("U1",),
        token="tok",
        created_at_utc="2026-08-08T06:00:00Z",
    )
    assert notice.permitted_actions == ("APPROVE", "REJECT")
    assert len(notice.token) < 2000  # Slack button `value` limit


def test_engine_notifies_the_gateway_on_pause() -> None:
    from chowki.config import ChowkiConfig
    from chowki.storage.memory import MemoryStorage

    gateway = InMemoryGateway()
    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), gateway=gateway))

    @workflow(engine=engine)
    def pipeline() -> None:
        pause(reason="review", payload={"a": 1}, permitted_actions=("APPROVE",))

    with pytest.raises(WorkflowPaused):
        pipeline(run_id="r1")

    assert len(gateway.notices) == 1
    notice, handle = gateway.notices[0]
    assert notice.run_id == "r1"
    assert notice.reason == "review"
    assert isinstance(handle, GatewayHandle)
    engine.close()


def test_gateway_receives_a_confirmation_after_resume() -> None:
    from chowki.config import ChowkiConfig
    from chowki.core.resume import resume
    from chowki.storage.memory import MemoryStorage

    gateway = InMemoryGateway()
    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), gateway=gateway))

    @workflow(engine=engine)
    def pipeline() -> str:
        pause(reason="review", permitted_actions=("APPROVE",))
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        pipeline(run_id="r2")
    token = excinfo.value.token
    assert token is not None
    resume(
        run_id="r2",
        token=token,
        decision=Decision.APPROVE,
        workflow_fn=pipeline,
        engine=engine,
    )

    assert gateway.confirmations
    handle, decision, _ = gateway.confirmations[0]
    assert isinstance(handle, GatewayHandle)
    assert decision is Decision.APPROVE
    engine.close()


def test_a_failing_gateway_does_not_lose_the_pause() -> None:
    """A broken Slack webhook must not destroy a durable run."""
    from chowki.config import ChowkiConfig
    from chowki.storage.memory import MemoryStorage
    from chowki.types import RunStatus

    class BrokenGateway(InMemoryGateway):
        def notify(self, notice: PauseNotice) -> GatewayHandle:
            raise RuntimeError("slack is down")

    engine = ChowkiEngine(ChowkiConfig(storage=MemoryStorage(), gateway=BrokenGateway()))

    @workflow(engine=engine)
    def pipeline() -> None:
        pause(reason="review")

    with pytest.raises(WorkflowPaused):
        pipeline(run_id="r3")

    run = engine.storage.get_run("r3")
    assert run is not None and run.status is RunStatus.PAUSED
    engine.close()


def test_verify_ingress_default_denies() -> None:
    """The base implementation must fail closed, not open."""
    gw = InMemoryGateway()
    assert gw.verify_ingress(body=b"{}", headers={}) is False


def test_console_gateway_writes_the_token_and_actions(capsys: pytest.CaptureFixture[str]) -> None:
    gw = ConsoleGateway()
    gw.notify(
        PauseNotice(
            run_id="r",
            workflow="w",
            step_id="s#0",
            reason="why",
            payload={},
            permitted_actions=("APPROVE", "REJECT"),
            reviewers=(),
            token="TOKEN123",
            created_at_utc="2026-08-08T06:00:00Z",
        )
    )
    out = capsys.readouterr().out
    assert "chowki" in out.lower()
    assert "TOKEN123" in out
    assert "APPROVE" in out and "REJECT" in out

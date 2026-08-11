# python/chowki/tests/unit/test_gateway.py
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

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


def test_verify_ingress_docstrings_mention_raw_bytes() -> None:
    for cls in (ChannelGateway, InMemoryGateway, ConsoleGateway):
        doc = cls.verify_ingress.__doc__
        assert doc is not None, f"{cls.__name__}.verify_ingress is missing a docstring"
        assert "raw `body` bytes" in doc
        assert "reserialisation breaks HMAC" in doc


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


def test_console_gateway_cli_hint_formatting(
    capsys: pytest.CaptureFixture[str], tmp_path: pytest.TempPathFactory
) -> None:
    from pathlib import Path

    from chowki.config import configure, reset_engine
    from chowki.core.registry import clear_registry, register_workflow

    # Default DB and unresolvable module -> plain chowki resume ...
    reset_engine()
    clear_registry()
    gw = ConsoleGateway()
    gw.notify(
        PauseNotice(
            run_id="run-def",
            workflow="plain_wf",
            step_id="s#0",
            reason="test",
            payload={},
            permitted_actions=("APPROVE",),
            reviewers=(),
            token="TOK1",
            created_at_utc="2026-08-11T00:00:00Z",
        )
    )
    out = capsys.readouterr().out
    assert "To resume via CLI: chowki resume run-def --token <above> --decision APPROVE" in out

    # Non-default DB and registered module -> chowki --db <path> -m <module> resume ...
    custom_db = Path(str(tmp_path)) / "custom_test.db"
    configure(db_path=custom_db)

    def dummy_wf() -> None:
        pass

    dummy_wf.__module__ = "my_mod.workflows"
    register_workflow("custom_wf", dummy_wf)

    gw.notify(
        PauseNotice(
            run_id="run-custom",
            workflow="custom_wf",
            step_id="s#0",
            reason="test",
            payload={},
            permitted_actions=("APPROVE",),
            reviewers=(),
            token="TOK2",
            created_at_utc="2026-08-11T00:00:00Z",
        )
    )
    out2 = capsys.readouterr().out
    expected_hint = (
        f"To resume via CLI: chowki --db {custom_db} -m my_mod.workflows resume run-custom"
        " --token <above> --decision APPROVE"
    )
    assert expected_hint in out2

    reset_engine()
    clear_registry()


def test_console_gateway_hint_uses_the_engine_that_paused_the_run(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run paused under a non-global engine must be resumable by the printed command."""
    from chowki.config import ChowkiConfig, reset_engine

    monkeypatch.chdir(tmp_path)
    reset_engine()
    custom_db = tmp_path / "mydb.db"
    engine = ChowkiEngine(ChowkiConfig(db_path=custom_db, gateway=ConsoleGateway()))

    @workflow(engine=engine)
    def pipeline() -> None:
        pause(reason="review", permitted_actions=("APPROVE",))

    with pytest.raises(WorkflowPaused):
        pipeline(run_id="run-p6")

    out = capsys.readouterr().out
    assert f"To resume via CLI: chowki --db {custom_db} " in out
    engine.close()


def _hint_after(label: str, out: str) -> str:
    return next(line.split(label, 1)[1].strip() for line in out.splitlines() if label in line)


def test_console_gateway_hints_are_shell_quoted_and_share_one_prefix(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    split_command: Callable[[str], list[str]],
) -> None:
    """Every printed command must be copy-paste executable, spaces in the path included.

    A hint that is not quoted tokenises into a truncated `--db` value, and a
    `reissue-token` hint without the `--db`/`-m` flags cannot reach the run at all.
    """
    from chowki.config import ChowkiConfig, reset_engine

    monkeypatch.chdir(tmp_path)
    reset_engine()
    custom_db = tmp_path / "db dir with spaces" / "mydb.db"
    engine = ChowkiEngine(ChowkiConfig(db_path=custom_db, gateway=ConsoleGateway()))

    @workflow(engine=engine)
    def spaced_pipeline() -> None:
        pause(reason="review", permitted_actions=("APPROVE",))

    with pytest.raises(WorkflowPaused):
        spaced_pipeline(run_id="run-spaced")
    out = capsys.readouterr().out
    engine.close()

    prefix = ["chowki", "--db", str(custom_db), "-m", spaced_pipeline.__module__]
    assert split_command(_hint_after("To resume via CLI:", out)) == [
        *prefix,
        "resume",
        "run-spaced",
        "--token",
        "<above>",
        "--decision",
        "APPROVE",
    ]
    assert split_command(_hint_after("Lost the token?", out)) == [
        *prefix,
        "reissue-token",
        "run-spaced",
    ]


def test_console_gateway_hint_names_the_script_for_main_module_workflows(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workflow defined in the entry script is importable as that script's stem."""
    from chowki.config import reset_engine
    from chowki.core.registry import register_workflow

    monkeypatch.chdir(tmp_path)
    reset_engine()

    def script_wf() -> None: ...

    script_wf.__module__ = "__main__"
    register_workflow("script_wf", script_wf)
    notice = PauseNotice(
        run_id="run-main",
        workflow="script_wf",
        step_id="s#0",
        reason="test",
        payload={},
        permitted_actions=("APPROVE",),
        reviewers=(),
        token="TOK4",
        created_at_utc="2026-08-11T00:00:00Z",
    )

    monkeypatch.setattr(sys, "argv", [str(tmp_path / "demo_script.py")])
    ConsoleGateway().notify(notice)
    out = capsys.readouterr().out
    assert _hint_after("To resume via CLI:", out).startswith("chowki -m demo_script resume")
    assert _hint_after("Lost the token?", out) == "chowki -m demo_script reissue-token run-main"

    # Nothing importable to name (`python -c ...`, a REPL) prints no `-m` at all.
    monkeypatch.setattr(sys, "argv", ["-c"])
    ConsoleGateway().notify(notice)
    out = capsys.readouterr().out
    assert _hint_after("To resume via CLI:", out).startswith("chowki resume run-main")
    assert _hint_after("Lost the token?", out) == "chowki reissue-token run-main"


def test_console_gateway_hint_creates_no_default_database(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Formatting a hint must not install a default engine nor open a database."""
    from chowki.config import reset_engine

    monkeypatch.chdir(tmp_path)
    reset_engine()
    ConsoleGateway().notify(
        PauseNotice(
            run_id="run-nodb",
            workflow="plain_wf",
            step_id="s#0",
            reason="test",
            payload={},
            permitted_actions=("APPROVE",),
            reviewers=(),
            token="TOK3",
            created_at_utc="2026-08-11T00:00:00Z",
        )
    )
    out = capsys.readouterr().out
    assert "To resume via CLI: chowki resume run-nodb --token <above> --decision APPROVE" in out
    assert not (tmp_path / ".chowki").exists()

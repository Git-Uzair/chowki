"""Integration test for Task 7: Flagship showcase agent example."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path for importing examples
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import chowki  # noqa: E402
from chowki.config import configure, reset_engine  # noqa: E402
from chowki.guardrails import GuardrailConfig  # noqa: E402
from chowki.types import RunStatus  # noqa: E402

pytestmark = pytest.mark.integration


def _run(
    cmd: list[str],
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command from the repo root, the working directory the README assumes."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, env=env, cwd=REPO_ROOT, check=False
    )


def run_example(
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run examples/python/agent_review.py as its own process."""
    return _run([sys.executable, "examples/python/agent_review.py", *args], extra_env)


def run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the `chowki` console script as its own process, as an operator would."""
    return _run([sys.executable, "-m", "chowki", *args])


def test_agent_showcase_flow(tmp_path: Path) -> None:
    from examples.python.agent_review import (
        agent_review_workflow,
        get_llm_call_count,
        reset_llm_call_count,
    )

    reset_llm_call_count()
    db_path = tmp_path / "showcase_test.db"

    engine = configure(
        db_path=db_path,
        resume_secret=b"test-showcase-32-byte-secret-key!",
        guardrails=GuardrailConfig(
            max_token_budget=2000,
            soft_budget_threshold=0.80,
        ),
    )

    run_id = "agent-showcase-run-1"

    # Step 1: Initial run with simulated crash after step 1. The trigger is an env var,
    # not an argument: arguments are persisted on the run record and replayed by
    # `rerun()`, so a crash flag passed as one would crash the recovery run too.
    os.environ["CHOWKI_CRASH_AFTER"] = "1"
    try:
        with pytest.raises(RuntimeError, match="Simulated mid-run crash after step 1"):
            agent_review_workflow(prompt="Audit repo security", run_id=run_id)
    finally:
        os.environ.pop("CHOWKI_CRASH_AFTER", None)

    # Verify that only step 1 LLM call executed before crash
    assert get_llm_call_count() == 1

    # Set status to RUNNING to simulate active/stalled run state for recovery
    crashed_run = engine.storage.get_run(run_id)
    assert crashed_run is not None
    crashed_run.status = RunStatus.RUNNING
    engine.storage.put_run(crashed_run)

    # Recover the stalled run back to PENDING status
    recovered = chowki.recover_runs(engine=engine)
    assert any(r.run_id == run_id for r in recovered)

    recovered_run = engine.storage.get_run(run_id)
    assert recovered_run is not None
    assert recovered_run.status == RunStatus.PENDING

    # Step 2: Rerun recovered workflow -> Step 1 memoised (no new LLM call), pauses at approval gate
    with pytest.raises(chowki.WorkflowPaused) as exc_info:
        chowki.rerun(run_id=run_id, engine=engine)

    token = exc_info.value.token
    assert token is not None
    assert exc_info.value.run_id == run_id
    # Step 1 was memoised (count stayed at 1 for step 1), step 3 LLM call ran -> count is 2
    assert get_llm_call_count() == 2

    # Step 3: Resume with EDIT decision and CHOWKI_CRASH_AFTER=3
    patch = [{"op": "replace", "path": "/draft/to", "value": "security-team@example.com"}]
    os.environ["CHOWKI_CRASH_AFTER"] = "3"
    try:
        with pytest.raises(RuntimeError, match="Simulated mid-run crash after step 3"):
            chowki.resume(
                run_id=run_id,
                token=token,
                decision=chowki.Decision.EDIT,
                patch=patch,
                engine=engine,
            )
    finally:
        os.environ.pop("CHOWKI_CRASH_AFTER", None)

    # Confirm run was in storage, set status to RUNNING for recovery, then recover
    crashed_run_2 = engine.storage.get_run(run_id)
    assert crashed_run_2 is not None
    crashed_run_2.status = RunStatus.RUNNING
    engine.storage.put_run(crashed_run_2)

    recovered_2 = chowki.recover_runs(engine=engine)
    assert any(r.run_id == run_id for r in recovered_2)

    recovered_run_2 = engine.storage.get_run(run_id)
    assert recovered_run_2 is not None
    assert recovered_run_2.status == RunStatus.PENDING

    # Step 4: Rerun recovered run -> completed steps skipped, LLM call count did NOT increase
    final_result = chowki.rerun(run_id=run_id, engine=engine)
    assert "Email sent to security-team@example.com" in final_result
    assert get_llm_call_count() == 2

    final_run = engine.storage.get_run(run_id)
    assert final_run is not None
    assert final_run.status == RunStatus.COMPLETED

    reset_engine()


def test_agent_showcase_cli_execution(tmp_path: Path) -> None:
    """Test full CLI execution of examples/python/agent_review.py with --crash-after 1."""
    db_path = tmp_path / "showcase_cli.db"
    res = run_example(["--crash-after", "1", "--db", str(db_path)])
    assert res.returncode == 0
    assert "Simulated crash after step 1" in res.stderr
    assert "Recovered 1 run(s) back to PENDING status." in res.stdout
    assert "Total LLM calls executed: 2" in res.stdout
    assert "Workflow finished: Email sent to security-team@example.com" in res.stdout


def test_agent_showcase_crash_env_var(tmp_path: Path) -> None:
    """CHOWKI_CRASH_AFTER=1 crashes the run, then auto-recovery finishes it with 2 LLM calls.

    The env var must not survive into `chowki.rerun`, or the rerun crashes again at
    the same step and the run never reaches the approval gate.
    """
    db_path = tmp_path / "showcase_env.db"
    res = run_example(["--db", str(db_path)], extra_env={"CHOWKI_CRASH_AFTER": "1"})
    assert res.returncode == 0
    assert "Simulated crash after step 1" in res.stderr
    assert "Recovered 1 run(s) back to PENDING status." in res.stdout
    assert "Total LLM calls executed: 2" in res.stdout
    assert "Workflow finished: Email sent to security-team@example.com" in res.stdout


def _llm_step_stamps(db_path: Path, run_id: str) -> dict[str, tuple[int, str | None]]:
    """Attempt count and end time of every executed LLM step, read from storage.

    Process-independent evidence of memoisation: re-executing a step rewrites its
    record, so unchanged stamps mean the LLM call did not happen a second time.
    """
    engine = configure(db_path=db_path)
    try:
        return {
            s.step_id: (s.attempts, s.ended_at_utc)
            for s in engine.storage.list_steps(run_id)
            if s.name in ("agent_plan", "agent_draft_email")
        }
    finally:
        reset_engine()


def _run_status(db_path: Path, run_id: str) -> RunStatus:
    engine = configure(db_path=db_path)
    try:
        run = engine.storage.get_run(run_id)
        assert run is not None
        return run.status
    finally:
        reset_engine()


def test_agent_showcase_manual_cli_recovery(tmp_path: Path) -> None:
    """`--no-auto-recover` leaves a stalled run for the documented operator CLI arc."""
    db_path = tmp_path / "showcase_manual.db"
    run_id = "showcase-agent-run-1"

    crashed = run_example(["--crash-after", "3", "--no-auto-recover", "--db", str(db_path)])
    assert crashed.returncode == 1
    assert "[CRASH SIMULATED]" in crashed.stdout
    assert "Total LLM calls executed before crash: 2" in crashed.stdout
    # The script must NOT recover in-process: that is the operator's job below.
    assert "Recovered" not in crashed.stdout
    assert _run_status(db_path, run_id) == RunStatus.RUNNING

    llm_steps_before = _llm_step_stamps(db_path, run_id)
    assert len(llm_steps_before) == 2  # two LLM calls total, both already accounted for

    cli_args = ["--db", str(db_path), "-m", "examples.python.agent_review"]

    # `chowki recover` finds the stalled RUNNING run and flips it back to PENDING.
    recovered = run_cli([*cli_args, "recover"])
    assert recovered.returncode == 0, recovered.stderr
    assert f"Recovered 1 run(s): {run_id}" in recovered.stdout

    # `chowki rerun` finishes it in a fresh process without repeating a single LLM call.
    reran = run_cli([*cli_args, "rerun", run_id])
    assert reran.returncode == 0, reran.stderr
    assert "Email sent to security-team@example.com" in reran.stdout
    assert _run_status(db_path, run_id) == RunStatus.COMPLETED
    assert _llm_step_stamps(db_path, run_id) == llm_steps_before

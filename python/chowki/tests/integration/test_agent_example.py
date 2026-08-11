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

    # Step 1: Initial run with simulated crash after step 1
    with pytest.raises(RuntimeError, match="Simulated mid-run crash after step 1"):
        agent_review_workflow(
            prompt="Audit repo security",
            crash_after_step=1,
            run_id=run_id,
        )

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

    # Step 3: Resume with EDIT decision and crash_after_step 3
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
    cmd = [
        sys.executable,
        "examples/python/agent_review.py",
        "--crash-after",
        "1",
        "--db",
        str(db_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    assert res.returncode == 0
    assert "Simulated crash after step 1" in res.stderr
    assert "Recovered 1 run(s) back to PENDING status." in res.stdout
    assert "Total LLM calls executed: 2" in res.stdout
    assert "Workflow finished: Email sent to security-team@example.com" in res.stdout

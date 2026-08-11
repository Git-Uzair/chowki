"""Integration test for Task 7: Flagship showcase agent example."""

from __future__ import annotations

import json
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


def _run_cli(
    args: list[str], extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[4]
    repo_src = repo_root / "python" / "chowki" / "src"
    env = os.environ.copy()
    pypath = [str(repo_src), str(repo_root)]
    if extra_env and "PYTHONPATH" in extra_env:
        pypath.append(extra_env["PYTHONPATH"])
    if env.get("PYTHONPATH"):
        pypath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pypath)
    if extra_env:
        for k, v in extra_env.items():
            if k != "PYTHONPATH":
                env[k] = v

    cmd = [sys.executable, "-m", "chowki", *args]
    return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)  # noqa: S603


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
    token = None

    # Step 1: Initial run -> pauses at email approval gate
    with pytest.raises(chowki.WorkflowPaused) as exc_info:
        agent_review_workflow(
            prompt="Audit repo security",
            run_id=run_id,
        )

    token = exc_info.value.token
    assert token is not None
    assert exc_info.value.run_id == run_id
    assert get_llm_call_count() == 2

    # Step 2: Resume via CLI with EDIT patch and crash_after=3 flag
    patch_str = json.dumps(
        [{"op": "replace", "path": "/draft/to", "value": "security-team@example.com"}]
    )
    res = _run_cli(
        [
            "--db",
            str(db_path),
            "-m",
            "examples.python.agent_review",
            "resume",
            run_id,
            "-t",
            token,
            "-d",
            "EDIT",
            "-p",
            patch_str,
        ],
        extra_env={
            "CHOWKI_RESUME_SECRET": "test-showcase-32-byte-secret-key!",
            "CHOWKI_CRASH_AFTER": "3",
        },
    )
    assert res.returncode != 0
    assert "Simulated crash" in res.stderr

    # Step 3: Confirm run is left in RUNNING status, then recover it
    crashed_run = engine.storage.get_run(run_id)
    assert crashed_run is not None
    assert crashed_run.status == RunStatus.RUNNING

    recovered = chowki.recover_runs(engine=engine)
    assert any(r.run_id == run_id for r in recovered)

    recovered_run = engine.storage.get_run(run_id)
    assert recovered_run is not None
    assert recovered_run.status == RunStatus.PENDING

    # Step 4: Rerun recovered run -> completed steps skipped, LLM call count did not grow
    final_result = chowki.rerun(run_id=run_id, engine=engine)
    assert "Email sent to security-team@example.com" in final_result
    assert get_llm_call_count() == 2

    final_run = engine.storage.get_run(run_id)
    assert final_run is not None
    assert final_run.status == RunStatus.COMPLETED

    reset_engine()

"""Integration tests for the `chowki` CLI console script."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import chowki
from chowki.config import configure, reset_engine
from chowki.errors import WorkflowPaused

pytestmark = pytest.mark.integration


def run_cli(
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_src = Path(__file__).resolve().parents[2] / "src"
    env = os.environ.copy()
    existing_pypath = env.get("PYTHONPATH", "")
    pypath_parts = [str(repo_src)]
    if extra_env and "PYTHONPATH" in extra_env:
        pypath_parts.append(extra_env["PYTHONPATH"])
    if existing_pypath:
        pypath_parts.append(existing_pypath)
    env["PYTHONPATH"] = os.pathsep.join(pypath_parts)

    if extra_env:
        for k, v in extra_env.items():
            if k != "PYTHONPATH":
                env[k] = v

    cmd = [sys.executable, "-m", "chowki", *args]
    return subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, env=env, cwd=cwd, check=False
    )


def test_cli_help_and_version() -> None:
    res = run_cli(["--help"])
    assert res.returncode == 0
    assert "chowki" in res.stdout.lower()

    res_v = run_cli(["--version"])
    assert res_v.returncode == 0
    assert chowki.__version__ in res_v.stdout


def test_cli_runs_list_and_show(tmp_path: Path) -> None:
    db_path = tmp_path / "chowki.db"

    @chowki.workflow
    def simple_wf() -> str:
        return "hello"

    configure(db_path=db_path)
    simple_wf(run_id="run-101")
    reset_engine()

    # runs list text
    res = run_cli(["--db", str(db_path), "runs", "list"])
    assert res.returncode == 0
    assert "run-101" in res.stdout

    # runs list --json
    res_json = run_cli(["--db", str(db_path), "--json", "runs", "list"])
    assert res_json.returncode == 0
    data: list[dict[str, object]] = json.loads(res_json.stdout)
    assert isinstance(data, list)
    assert any(r.get("run_id") == "run-101" for r in data)

    # runs show text
    res_show = run_cli(["--db", str(db_path), "runs", "show", "run-101"])
    assert res_show.returncode == 0
    assert "run-101" in res_show.stdout

    # runs show --json
    res_show_json = run_cli(["--db", str(db_path), "--json", "runs", "show", "run-101"])
    assert res_show_json.returncode == 0
    data_show = json.loads(res_show_json.stdout)
    assert data_show["run"]["run_id"] == "run-101"


def test_cli_resume_and_reissue_token(tmp_path: Path) -> None:
    db_path = tmp_path / "chowki.db"

    # Create fixture module
    mod_path = tmp_path / "fixture_wf.py"
    mod_path.write_text(
        "import chowki\n"
        "\n"
        "@chowki.workflow\n"
        "def sync_paused_wf() -> str:\n"
        "    res = chowki.pause(reason='need approval', payload={'amount': 100})\n"
        "    return f'approved: {res}'\n",
        encoding="utf-8",
    )

    # Start and pause workflow in python
    import importlib.util

    spec = importlib.util.spec_from_file_location("fixture_wf", mod_path)
    assert spec is not None and spec.loader is not None
    fixture_wf = importlib.util.module_from_spec(spec)
    sys.modules["fixture_wf"] = fixture_wf
    spec.loader.exec_module(fixture_wf)

    configure(db_path=db_path, resume_secret=b"test-secret-32-bytes-long-123456")
    with contextlib.suppress(WorkflowPaused):
        fixture_wf.sync_paused_wf(run_id="run-paused-1")
    reset_engine()

    extra_env = {
        "PYTHONPATH": str(tmp_path),
        "CHOWKI_RESUME_SECRET": "test-secret-32-bytes-long-123456",
    }

    # reissue token via CLI
    res_reissue = run_cli(
        ["--db", str(db_path), "-m", "fixture_wf", "reissue-token", "run-paused-1"],
        extra_env=extra_env,
    )
    assert res_reissue.returncode == 0
    token = res_reissue.stdout.strip()
    assert len(token) > 10

    # resume via CLI
    res_resume = run_cli(
        [
            "--db",
            str(db_path),
            "-m",
            "fixture_wf",
            "resume",
            "run-paused-1",
            "--token",
            token,
            "--decision",
            "APPROVE",
        ],
        extra_env=extra_env,
    )
    assert res_resume.returncode == 0
    assert "approved" in res_resume.stdout

    # check run status is COMPLETED
    res_show = run_cli(["--db", str(db_path), "--json", "runs", "show", "run-paused-1"])
    assert res_show.returncode == 0
    data = json.loads(res_show.stdout)
    assert data["run"]["status"] == "COMPLETED"


def test_cli_async_workflow_resume(tmp_path: Path) -> None:
    db_path = tmp_path / "chowki.db"

    mod_path = tmp_path / "fixture_async_wf.py"
    mod_path.write_text(
        "import chowki\n"
        "\n"
        "@chowki.workflow\n"
        "async def async_paused_wf() -> str:\n"
        "    res = chowki.pause(reason='async gate', payload={'val': 42})\n"
        "    return f'async done: {res}'\n",
        encoding="utf-8",
    )

    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location("fixture_async_wf", mod_path)
    assert spec is not None and spec.loader is not None
    fixture_async_wf = importlib.util.module_from_spec(spec)
    sys.modules["fixture_async_wf"] = fixture_async_wf
    spec.loader.exec_module(fixture_async_wf)

    configure(db_path=db_path, resume_secret=b"test-secret-32-bytes-long-123456")
    with contextlib.suppress(WorkflowPaused):
        asyncio.run(fixture_async_wf.async_paused_wf(run_id="run-async-1"))
    reset_engine()

    extra_env = {
        "PYTHONPATH": str(tmp_path),
        "CHOWKI_RESUME_SECRET": "test-secret-32-bytes-long-123456",
    }

    res_reissue = run_cli(
        ["--db", str(db_path), "-m", "fixture_async_wf", "reissue-token", "run-async-1"],
        extra_env=extra_env,
    )
    assert res_reissue.returncode == 0
    token = res_reissue.stdout.strip()

    res_resume = run_cli(
        [
            "--db",
            str(db_path),
            "-m",
            "fixture_async_wf",
            "resume",
            "run-async-1",
            "--token",
            token,
            "--decision",
            "APPROVE",
        ],
        extra_env=extra_env,
    )
    assert res_resume.returncode == 0
    assert "async done" in res_resume.stdout


def test_cli_release_and_complete_step(tmp_path: Path) -> None:
    db_path = tmp_path / "chowki.db"

    eng = configure(db_path=db_path)
    from chowki.types import StepRecord, StepStatus

    rec = StepRecord(
        run_id="run-x",
        step_id="s#0",
        name="my_step",
        ordinal=0,
        idempotency_key="claim-key-1",
        args_hash="hash1",
        started_at_utc="2026-08-11T00:00:00Z",
        status=StepStatus.RUNNING,
    )
    eng.storage.put_step(rec)
    eng.storage.claim_idempotency_key("claim-key-1", args_hash="hash1")
    reset_engine()

    res_rel = run_cli(["--db", str(db_path), "release-step", "run-x", "s#0"])
    assert res_rel.returncode == 0

    res_comp = run_cli(
        ["--db", str(db_path), "complete-step", "run-x", "s#0", "--result", '{"status": "ok"}']
    )
    assert res_comp.returncode == 0


def test_cli_recover_and_rerun(tmp_path: Path) -> None:
    db_path = tmp_path / "chowki.db"

    mod_path = tmp_path / "fixture_recover.py"
    mod_path.write_text(
        "import chowki\n\n@chowki.workflow\ndef rec_wf() -> str:\n    return 'rec finished'\n",
        encoding="utf-8",
    )

    import importlib.util

    spec = importlib.util.spec_from_file_location("fixture_recover", mod_path)
    assert spec is not None and spec.loader is not None
    fixture_recover = importlib.util.module_from_spec(spec)
    sys.modules["fixture_recover"] = fixture_recover
    spec.loader.exec_module(fixture_recover)

    eng = configure(db_path=db_path)
    from chowki.types import RunRecord, RunStatus

    eng.storage.put_run(
        RunRecord(
            run_id="run-rec-1",
            workflow="rec_wf",
            tenant_id="default",
            status=RunStatus.RUNNING,
            created_at_utc="2026-08-11T00:00:00Z",
            updated_at_utc="2026-08-11T00:00:00Z",
        )
    )
    eng.close()
    reset_engine()

    extra_env = {"PYTHONPATH": str(tmp_path)}

    res_rec = run_cli(["--db", str(db_path), "recover"], extra_env=extra_env)
    assert res_rec.returncode == 0
    assert "run-rec-1" in res_rec.stdout

    res_rerun = run_cli(
        ["--db", str(db_path), "-m", "fixture_recover", "rerun", "run-rec-1"],
        extra_env=extra_env,
    )
    assert res_rerun.returncode == 0
    assert "rec finished" in res_rerun.stdout


def test_console_hint_command_resumes_a_run_paused_under_a_custom_engine(
    tmp_path: Path, split_command: Callable[[str], list[str]]
) -> None:
    """The command the console gateway prints must work verbatim, not just look plausible.

    The run is paused under `@chowki.workflow(engine=...)` with a non-default database, so a
    hint missing `--db` would send the CLI at the default database and exit 1. The path
    contains a space, so an unquoted hint tokenises into a truncated `--db` value.
    """
    db_path = tmp_path / "custom dir" / "mydb.db"
    secret = "test-secret-32-bytes-long-123456"

    mod_path = tmp_path / "fixture_engine_wf.py"
    mod_path.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "\n"
        "import chowki\n"
        "from chowki.config import ChowkiConfig, ChowkiEngine\n"
        "from chowki.errors import WorkflowPaused\n"
        "from chowki.hitl.console import ConsoleGateway\n"
        "\n"
        "ENGINE = ChowkiEngine(\n"
        "    ChowkiConfig(\n"
        "        db_path=Path(os.environ['CHOWKI_DB']),\n"
        "        gateway=ConsoleGateway(),\n"
        "        resume_secret=os.environ['CHOWKI_RESUME_SECRET'],\n"
        "    )\n"
        ")\n"
        "\n"
        "@chowki.workflow(engine=ENGINE)\n"
        "def engine_bound_wf() -> str:\n"
        "    res = chowki.pause(reason='need approval', payload={'amount': 100})\n"
        "    return f'approved: {res}'\n"
        "\n"
        "def start() -> None:\n"
        "    try:\n"
        "        engine_bound_wf(run_id='run-eng-1')\n"
        "    except WorkflowPaused:\n"
        "        pass\n",
        encoding="utf-8",
    )

    extra_env = {
        "PYTHONPATH": str(tmp_path),
        "CHOWKI_DB": str(db_path),
        "CHOWKI_RESUME_SECRET": secret,
    }
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(__file__).resolve().parents[2] / "src"), str(tmp_path), env.get("PYTHONPATH", "")]
    )
    env.update({k: v for k, v in extra_env.items() if k != "PYTHONPATH"})

    paused = subprocess.run(
        [sys.executable, "-c", "import fixture_engine_wf as m; m.start()"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert paused.returncode == 0, paused.stderr

    hint = next(
        line.split("To resume via CLI:", 1)[1].strip()
        for line in paused.stdout.splitlines()
        if "To resume via CLI:" in line
    )
    token = next(
        line.split(":", 1)[1].strip()
        for line in paused.stdout.splitlines()
        if line.startswith("Resume Token:")
    )
    argv = split_command(hint)
    assert argv[:5] == ["chowki", "--db", str(db_path), "-m", "fixture_engine_wf"]
    argv = [token if part == "<above>" else part for part in argv[1:]]

    # No default database may be created in the working directory by either process.
    assert not (tmp_path / ".chowki").exists()

    resumed = run_cli(argv, extra_env=extra_env, cwd=tmp_path)
    assert resumed.returncode == 0, resumed.stderr
    assert "approved" in resumed.stdout


def test_cli_error_handling(tmp_path: Path) -> None:
    db_path = tmp_path / "chowki.db"
    res = run_cli(["--db", str(db_path), "runs", "show", "nonexistent-run-id"])
    assert res.returncode == 1
    assert "Error:" in res.stderr

    # Test invalid module import catches ModuleNotFoundError and prints clean error
    res_mod = run_cli(["-m", "nonexistent_module_xyz", "runs", "list"])
    assert res_mod.returncode == 1
    assert "Error:" in res_mod.stderr
    assert "nonexistent_module_xyz" in res_mod.stderr
    assert "Traceback" not in res_mod.stderr

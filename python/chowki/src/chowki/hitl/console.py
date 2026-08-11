"""Console HITL gateway implementation."""

from __future__ import annotations

import os
import shlex
import subprocess  # `list2cmdline` only; nothing here spawns a process
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from chowki.hitl.gateway import ChannelAction, GatewayHandle, PauseNotice
from chowki.types import Decision, JSONObject


def _format_command(argv: Sequence[str]) -> str:
    """Render argv as a command the local shell can run verbatim.

    Quoting is per-platform on purpose: `shlex` quotes for POSIX shells, while `cmd.exe`
    does not understand its single quotes and needs `list2cmdline` instead. Without this a
    database path containing spaces tokenises into a truncated `--db` value.
    """
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _script_module_name() -> str | None:
    """Importable name of the entry script, for workflows defined in `__main__`.

    `python demo.py` can be reached again as `-m demo`; a REPL, `python -c ...`, or a
    console-script wrapper has no `.py` entry point and gets no `-m` at all.
    """
    script = sys.argv[0] if sys.argv else ""
    if not script:
        return None
    stem = Path(script).stem
    return stem if Path(script).suffix == ".py" and stem.isidentifier() else None


def _get_workflow_module(workflow_name: str) -> str | None:
    from chowki.core.registry import get_workflow

    wf = get_workflow(workflow_name)
    mod = getattr(wf, "__module__", None) if wf is not None else None
    if mod == "__main__":
        mod = _script_module_name()
    if mod and mod != "builtins":
        return str(mod)
    if "." in workflow_name:
        parts = workflow_name.rsplit(".", 1)
        if parts[0]:
            return parts[0]
    return None


def _get_non_default_db_path() -> Path | None:
    """Database file the printed CLI command must be pointed at, if not the default one.

    The engine that paused *this* run is asked first, so a run under
    ``@chowki.workflow(engine=...)`` prints the command that can actually reach it; the
    process-global engine is consulted only if one already exists. Nothing here creates
    an engine or touches the filesystem: printing a notice must not open a database.
    """
    from chowki.config import active_engine
    from chowki.core.context import current_run, in_run
    from chowki.storage import DEFAULT_DB_PATH

    engine = current_run().engine if in_run() else active_engine()
    if engine is None:
        return None
    # Read off the adapter, not the config: an engine built with an explicit
    # `storage=SQLiteStorage(path)` keeps the default `config.db_path`. Adapters with no
    # file at all (MemoryStorage) have no attribute and get no `--db`.
    raw = getattr(engine.storage, "db_path", None)
    if not isinstance(raw, (str, Path)):
        return None
    db_path = Path(raw)
    return None if db_path == DEFAULT_DB_PATH else db_path


class ConsoleGateway:
    """Zero-config console gateway printing pause notices to stdout.

    `verify_ingress` takes raw `body` bytes because reserialisation breaks HMAC signature
    verification.
    """

    name: str = "console"

    def notify(self, notice: PauseNotice) -> GatewayHandle:
        handle = GatewayHandle(channel="console", message_id=notice.run_id)
        # One prefix for every command printed below: a run under a non-default database or
        # a workflow the CLI has to import is unreachable without these flags, and that is
        # as true of `reissue-token` as it is of `resume`.
        prefix = ["chowki"]
        db_path = _get_non_default_db_path()
        if db_path is not None:
            prefix += ["--db", str(db_path)]
        mod_name = _get_workflow_module(notice.workflow)
        if mod_name is not None:
            prefix += ["-m", mod_name]
        resume_cmd = _format_command(
            [*prefix, "resume", notice.run_id, "--token", "<above>", "--decision", "APPROVE"]
        )
        reissue_cmd = _format_command([*prefix, "reissue-token", notice.run_id])

        print("=" * 60)
        print(" CHOWKI WORKFLOW PAUSED")
        print("=" * 60)
        print(f"Run ID:            {notice.run_id}")
        print(f"Workflow:          {notice.workflow}")
        print(f"Step ID:           {notice.step_id}")
        print(f"Reason:            {notice.reason}")
        print(f"Payload:           {notice.payload}")
        print(f"Permitted Actions: {', '.join(notice.permitted_actions)}")
        print(f"Reviewers:         {', '.join(notice.reviewers) if notice.reviewers else 'None'}")
        print(f"Resume Token:      {notice.token}")
        print("=" * 60)
        print(f"To resume via CLI: {resume_cmd}")
        print(
            f"To resume in Python: chowki.resume(run_id={notice.run_id!r}, token=<above>,"
            " decision=chowki.Decision.APPROVE)"
        )
        print(f"Lost the token? {reissue_cmd}")
        print("=" * 60)
        return handle

    def confirm(
        self, handle: GatewayHandle, decision: Decision, *, actor: JSONObject | None = None
    ) -> None:
        act_str = str(actor) if actor else "system"
        print(
            f"[ConsoleGateway] Run confirmed: handle={handle.message_id}, decision={decision},"
            f" actor={act_str}"
        )

    def verify_ingress(self, *, body: bytes, headers: Mapping[str, str]) -> bool:
        """Verify authenticity of inbound channel webhooks.

        Takes raw `body` bytes because reserialisation breaks HMAC signature verification.
        Always returns `False` as the console gateway has no ingress.
        """
        return False

    def parse_action(self, *, body: bytes, headers: Mapping[str, str]) -> ChannelAction | None:
        """Parse action payload received from an inbound channel webhook."""
        return None

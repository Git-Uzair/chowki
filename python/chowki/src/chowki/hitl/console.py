"""Console HITL gateway implementation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from chowki.hitl.gateway import ChannelAction, GatewayHandle, PauseNotice
from chowki.types import Decision, JSONObject


def _get_workflow_module(workflow_name: str) -> str | None:
    from chowki.core.registry import get_workflow

    wf = get_workflow(workflow_name)
    if wf is not None:
        mod = getattr(wf, "__module__", None)
        if mod and mod not in ("__main__", "builtins"):
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
        cli_parts = ["chowki"]
        db_path = _get_non_default_db_path()
        if db_path is not None:
            cli_parts.append(f"--db {db_path}")
        mod_name = _get_workflow_module(notice.workflow)
        if mod_name is not None:
            cli_parts.append(f"-m {mod_name}")
        cli_parts.append(f"resume {notice.run_id} --token <above> --decision APPROVE")
        cli_cmd = " ".join(cli_parts)

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
        print(f"To resume via CLI: {cli_cmd}")
        print(
            f"To resume in Python: chowki.resume(run_id={notice.run_id!r}, token=<above>,"
            " decision=chowki.Decision.APPROVE)"
        )
        print(f"Lost the token? chowki reissue-token {notice.run_id}")
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

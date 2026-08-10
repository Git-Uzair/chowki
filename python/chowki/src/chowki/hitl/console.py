"""Console HITL gateway implementation."""

from __future__ import annotations

from collections.abc import Mapping

from chowki.hitl.gateway import ChannelAction, GatewayHandle, PauseNotice
from chowki.types import Decision, JSONObject


class ConsoleGateway:
    """Zero-config console gateway printing pause notices to stdout.

    `verify_ingress` takes raw `body` bytes because reserialisation breaks HMAC signature
    verification.
    """

    name: str = "console"

    def notify(self, notice: PauseNotice) -> GatewayHandle:
        handle = GatewayHandle(channel="console", message_id=notice.run_id)
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
        print(
            f"To resume: chowki resume --run-id {notice.run_id} --token {notice.token}"
            " --decision APPROVE"
        )
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

"""HITL channel gateway interface and in-memory reference implementation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

import msgspec

from chowki.types import Decision, JSONObject


def _empty_patch_list() -> list[dict[str, Any]]:
    return []


def _empty_json_object() -> JSONObject:
    return {}


class PauseNotice(msgspec.Struct, kw_only=True, frozen=True):
    """Event payload emitted to a HITL channel gateway when a run pauses."""

    run_id: str
    workflow: str
    step_id: str
    reason: str
    payload: JSONObject
    permitted_actions: tuple[str, ...]
    reviewers: tuple[str, ...]
    token: str
    created_at_utc: str
    channel: str = "console"


class GatewayHandle(msgspec.Struct, kw_only=True, frozen=True):
    """Opaque, serialisable pointer to the message a gateway posted.

    Slack fills channel/message_id (ts) and response_url; Teams fills message_id
    (activity id); REST fills url. Persisted with the run so a confirmation can be
    delivered after a process restart.
    """

    channel: str
    message_id: str = ""
    conversation_id: str = ""
    response_url: str = ""
    expires_at_epoch: int = 0


class ChannelAction(msgspec.Struct, kw_only=True, frozen=True):
    """Parsed action payload received from an inbound channel webhook."""

    token: str
    decision: Decision
    patch: list[dict[str, Any]] = msgspec.field(default_factory=_empty_patch_list)
    actor: JSONObject = msgspec.field(default_factory=_empty_json_object)
    handle: GatewayHandle | None = None


@runtime_checkable
class ChannelGateway(Protocol):
    """Pluggable channel interface for Human-in-the-Loop notifications and ingress.

    `verify_ingress` takes raw `body` bytes because reserialisation breaks HMAC signature
    verification.
    """

    name: str

    def notify(self, notice: PauseNotice) -> GatewayHandle: ...

    def confirm(
        self, handle: GatewayHandle, decision: Decision, *, actor: JSONObject | None = None
    ) -> None: ...

    def verify_ingress(self, *, body: bytes, headers: Mapping[str, str]) -> bool:
        """Verify authenticity of inbound channel webhooks.

        Takes raw `body` bytes, never a parsed body, because reserialisation breaks HMAC
        signature verification.
        """
        ...

    def parse_action(self, *, body: bytes, headers: Mapping[str, str]) -> ChannelAction | None:
        """Parse action payload received from an inbound channel webhook."""
        ...


class InMemoryGateway:
    """In-process reference gateway recording notices and confirmations."""

    name: str = "in_memory"

    def __init__(self) -> None:
        self.notices: list[tuple[PauseNotice, GatewayHandle]] = []
        self.confirmations: list[tuple[GatewayHandle, Decision, JSONObject]] = []

    def notify(self, notice: PauseNotice) -> GatewayHandle:
        handle = GatewayHandle(channel="in_memory", message_id=notice.run_id)
        self.notices.append((notice, handle))
        return handle

    def confirm(
        self, handle: GatewayHandle, decision: Decision, *, actor: JSONObject | None = None
    ) -> None:
        self.confirmations.append((handle, decision, actor or {}))

    def verify_ingress(self, *, body: bytes, headers: Mapping[str, str]) -> bool:
        """Verify authenticity of inbound channel webhooks.

        Takes raw `body` bytes because reserialisation breaks HMAC signature verification.
        Returns `False` to deny all ingress by default.
        """
        return False

    def parse_action(self, *, body: bytes, headers: Mapping[str, str]) -> ChannelAction | None:
        """Parse action payload received from an inbound channel webhook."""
        return None

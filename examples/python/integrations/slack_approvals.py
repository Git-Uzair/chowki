"""chowki + Slack approvals, end to end — pause in Slack, resume over HTTP.

    pip install chowki fastapi uvicorn slack-sdk

> **A first-party Slack adapter is roadmap Phase 4 and is not shipped.** This file is what
> you write *today* to get the same result: chowki's `ChannelGateway` protocol is the
> supported extension point, and every hook used here — `notify`, `verify_ingress`,
> `parse_action` — exists now. When the built-in adapter lands you will be able to delete
> most of this; the workflow code above it will not change.

The shape:

    workflow pauses ──> SlackGateway.notify()  ──> Block Kit message with Approve/Reject
                                                    buttons carrying the resume token
    reviewer clicks ──> Slack POSTs your endpoint ──> verify_ingress() (HMAC)
                                                  ──> parse_action()  (payload -> decision)
                                                  ──> chowki.resume() (workflow continues)

Three things worth knowing before you run this in production, none of which chowki can do
for you:

1. `reviewers` is carried, not enforced. The resume token authorises a *run and gate*, not
   a person, so check who clicked before resuming — `_is_authorised` below.
2. `resume()` re-executes the workflow body in *this* process. Anything slow after the gate
   belongs in a background task, or Slack times the request out at 3 seconds.
3. One process resumes a given run at a time (single-writer per run).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
from typing import Any

import chowki
from chowki.hitl.gateway import ChannelAction, GatewayHandle, PauseNotice

try:
    from fastapi import BackgroundTasks, FastAPI, Request, Response
    from slack_sdk import WebClient
except ImportError as exc:  # pragma: no cover - example dependency
    raise SystemExit(
        "This example needs FastAPI and the Slack SDK:  pip install fastapi uvicorn slack-sdk"
    ) from exc

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_CHANNEL = os.environ.get("SLACK_APPROVALS_CHANNEL", "#approvals")

#: Slack rejects a request it considers stale; five minutes is Slack's own guidance and is
#: what stops a captured request being replayed later.
_MAX_REQUEST_AGE_SECONDS = 60 * 5

#: Who may approve. In a real deployment this comes from your directory, not a constant.
_APPROVERS = frozenset(os.environ.get("CHOWKI_APPROVERS", "").split(",")) - {""}


# --- The gateway: chowki's supported extension point ------------------------------------


class SlackGateway:
    """A `ChannelGateway` that posts approval gates to Slack and accepts button clicks.

    `ConsoleGateway` is the reference implementation of this same protocol — nothing here
    is privileged, it is the ordinary way to add a channel.
    """

    name = "slack"

    def __init__(self, client: WebClient, channel: str = SLACK_CHANNEL) -> None:
        self._client = client
        self._channel = channel

    def notify(self, notice: PauseNotice) -> GatewayHandle:
        """Post the gate. The token rides on each button's `value`.

        `PauseNotice` asserts the token stays under 2000 characters precisely because that
        is Slack's limit for a button `value` — it is sized to fit this.
        """
        response = self._client.chat_postMessage(
            channel=self._channel,
            text=f"Approval needed: {notice.reason}",
            blocks=self._blocks(notice),
        )
        return GatewayHandle(channel="slack", message_id=str(response["ts"]))

    def confirm(self, handle: GatewayHandle, decision: Any, *, actor: Any = None) -> None:
        """Called after a successful resume — close the loop in the thread."""
        self._client.chat_postMessage(
            channel=self._channel,
            thread_ts=handle.message_id,
            text=f"Resolved: {decision} by {actor or 'system'}",
        )

    def verify_ingress(self, *, body: bytes, headers: dict[str, str]) -> bool:
        """Verify authenticity of inbound channel webhooks.

        Takes raw `body` bytes because reserialisation breaks HMAC signature verification —
        re-encoding the JSON changes the bytes Slack signed and every request then fails.
        """
        if not SLACK_SIGNING_SECRET:
            return False
        timestamp = headers.get("x-slack-request-timestamp", "")
        signature = headers.get("x-slack-signature", "")
        if not timestamp or not signature:
            return False
        try:
            age = abs(time.time() - int(timestamp))
        except ValueError:
            return False
        if age > _MAX_REQUEST_AGE_SECONDS:
            return False  # replayed or clock-skewed
        basestring = b"v0:" + timestamp.encode() + b":" + body
        expected = (
            "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), basestring, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(expected, signature)

    def parse_action(self, *, body: bytes, headers: dict[str, str]) -> ChannelAction | None:
        """Turn Slack's interactive payload into a decision chowki understands."""
        del headers
        form = urllib.parse.parse_qs(body.decode("utf-8"))
        raw = form.get("payload", [""])[0]
        if not raw:
            return None
        payload: dict[str, Any] = json.loads(raw)
        actions = payload.get("actions") or []
        if not actions:
            return None
        action = actions[0]
        try:
            decision = chowki.Decision[str(action.get("action_id", "")).upper()]
        except KeyError:
            return None
        # `ChannelAction` carries no run_id — the token is scope-bound to the run and gate,
        # but `resume()` still wants the id explicitly, so route it through `actor`. The
        # button's enclosing block_id is where it was stashed on the way out.
        return ChannelAction(
            token=str(action.get("value", "")),
            decision=decision,
            actor={
                "run_id": str(payload.get("actions", [{}])[0].get("block_id", "")),
                "slack_user_id": str(payload.get("user", {}).get("id", "")),
            },
        )

    @staticmethod
    def _blocks(notice: PauseNotice) -> list[dict[str, Any]]:
        buttons = [
            {
                "type": "button",
                "action_id": act,  # -> ChannelAction.decision
                "text": {"type": "plain_text", "text": act.title()},
                "value": notice.token,  # -> ChannelAction.token
                "style": {"APPROVE": "primary", "REJECT": "danger"}.get(act, "default"),
            }
            for act in notice.permitted_actions
        ]
        return [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{notice.reason}*\n`{notice.run_id}`"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```{json.dumps(notice.payload, indent=2)}```"},
            },
            # block_id carries the run id back on the click -> actor["run_id"]
            {"type": "actions", "block_id": notice.run_id, "elements": buttons},
        ]


# --- The ingress endpoint ----------------------------------------------------------------

app = FastAPI()
gateway = SlackGateway(WebClient(token=os.environ.get("SLACK_BOT_TOKEN", "")))


def _is_authorised(actor: dict[str, Any]) -> bool:
    """chowki does not do this for you.

    `reviewers` on a pause is carried to the gateway but never enforced: the resume token
    authorises a run and a gate, not an identity. Anyone who obtains the token can spend
    it, so the check for *who* clicked belongs here, before resume() is called.
    """
    return not _APPROVERS or str(actor.get("slack_user_id", "")) in _APPROVERS


@app.post("/slack/interactivity")
async def slack_interactivity(request: Request, background: BackgroundTasks) -> Response:
    """Slack posts here when a reviewer clicks a button."""
    body = await request.body()  # raw bytes: re-encoding would break the signature
    headers = {k.lower(): v for k, v in request.headers.items()}

    if not gateway.verify_ingress(body=body, headers=headers):
        return Response(status_code=401)

    action = gateway.parse_action(body=body, headers=headers)
    if action is None:
        return Response(status_code=400)

    if not _is_authorised(action.actor or {}):
        return Response("You are not an approver for this gate.", status_code=403)

    # Slack times the request out after 3 seconds, and resume() re-executes the workflow
    # body in this process — so acknowledge now and continue the run in the background.
    run_id = str((action.actor or {}).get("run_id", ""))
    if not run_id:
        return Response("No run id on the action payload.", status_code=400)

    background.add_task(_resume, run_id, action.token, action.decision, dict(action.actor))
    return Response("Decision recorded — the run is continuing.", status_code=200)


def _resume(run_id: str, token: str, decision: chowki.Decision, actor: dict[str, Any]) -> None:
    try:
        chowki.resume(run_id=run_id, token=token, decision=decision, actor=actor)
    except chowki.HumanRejectedError:
        pass  # a REJECT is a normal outcome, not a failure
    except chowki.ChowkiError:
        # Replayed or expired token, or a decision the gate did not permit. Surface this
        # to your own alerting; `chowki reissue-token <run_id>` mints a fresh one.
        raise


def main() -> None:
    chowki.configure(
        db_path="./slack_approvals.db",
        gateway=gateway,
        resume_secret=os.environ.get("CHOWKI_RESUME_SECRET", "").encode() or None,
    )
    print("Configured. Run the API with:  uvicorn slack_approvals:app --reload")
    print("Point Slack's Interactivity Request URL at POST /slack/interactivity")


if __name__ == "__main__":
    main()

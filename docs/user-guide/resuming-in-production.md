# Resuming Workflows in Production: Web App Integration

`chowki` does not serve HTTP endpoints itself. Instead, you integrate approval routes directly into your existing web application (FastAPI, Flask, Django, etc.) and call `chowki.aresume()` or `chowki.resume()` within your route handlers.

The HMAC-signed resume token acts as scope-bound authorization for the specific run and step.

---

## Exception to HTTP Status Mapping

Your web endpoint should handle chowki resume exceptions and map them to standard HTTP status codes:

| Exception | HTTP Status | Response Body | Description |
|---|---|---|---|
| `InvalidResumeToken` | `401 Unauthorized` | `{"error": "..."}` | Signature mismatch, invalid token format, or scope mismatch |
| `ExpiredResumeToken` | `410 Gone` | `{"error": "..."}` | Token TTL expired (default 24h); use `reissue_token()` |
| `ReplayedNonceError` | `409 Conflict` | `{"error": "..."}` | Token nonce was already consumed (single-use enforcement) |
| `ChowkiStateError` | `404 Not Found` | `{"error": "..."}` | Run ID not found or run is not in `PAUSED` status |
| `HumanRejectedError` | `200 OK` | `{"outcome": "rejected"}` | Human decision was `REJECT`; run transitioned to `REJECTED` |
| `WorkflowPaused` | `202 Accepted` | `{"outcome": "paused", "token": "...", "step_id": "..."}` | Workflow re-executed and hit another pause gate or `ESCALATE` |
| *Success* | `200 OK` | `{"outcome": "completed", "value": ...}` | Workflow re-executed to completion and returned a value |

---

## FastAPI Recipe (Async)

Use `chowki.aresume()` in async frameworks like FastAPI:

```python
from typing import Any
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import chowki
from chowki.errors import (
    InvalidResumeToken,
    ExpiredResumeToken,
    ReplayedNonceError,
    ChowkiStateError,
    HumanRejectedError,
    WorkflowPaused,
)
from chowki.types import Decision

app = FastAPI()


class ResumePayload(BaseModel):
    run_id: str
    token: str
    decision: str  # "APPROVE", "REJECT", "EDIT", "ESCALATE"
    patch: list[dict[str, Any]] | None = None
    note: str | None = None


@app.post("/api/v1/resume")
async def resume_workflow(payload: ResumePayload) -> Response:
    try:
        res = await chowki.aresume(
            run_id=payload.run_id,
            token=payload.token,
            decision=Decision(payload.decision),
            patch=payload.patch,
            note=payload.note,
        )
        return JSONResponse(status_code=200, content={"outcome": "completed", "value": res.value})
    except InvalidResumeToken as exc:
        return JSONResponse(status_code=401, content={"error": str(exc)})
    except ExpiredResumeToken as exc:
        return JSONResponse(status_code=410, content={"error": str(exc)})
    except ReplayedNonceError as exc:
        return JSONResponse(status_code=409, content={"error": str(exc)})
    except ChowkiStateError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except HumanRejectedError:
        return JSONResponse(status_code=200, content={"outcome": "rejected"})
    except WorkflowPaused as exc:
        return JSONResponse(
            status_code=202,
            content={"outcome": "paused", "token": exc.token, "step_id": exc.step_id},
        )
```

---

## Flask Recipe (Sync)

Use `chowki.resume()` in synchronous WSGI frameworks like Flask:

```python
from flask import Flask, request, jsonify
import chowki
from chowki.errors import (
    InvalidResumeToken,
    ExpiredResumeToken,
    ReplayedNonceError,
    ChowkiStateError,
    HumanRejectedError,
    WorkflowPaused,
)
from chowki.types import Decision

app = Flask(__name__)


@app.post("/api/v1/resume")
def resume_workflow():
    payload = request.get_json() or {}
    try:
        res = chowki.resume(
            run_id=payload["run_id"],
            token=payload["token"],
            decision=Decision(payload["decision"]),
            patch=payload.get("patch"),
            note=payload.get("note"),
        )
        return jsonify({"outcome": "completed", "value": res.value}), 200
    except InvalidResumeToken as exc:
        return jsonify({"error": str(exc)}), 401
    except ExpiredResumeToken as exc:
        return jsonify({"error": str(exc)}), 410
    except ReplayedNonceError as exc:
        return jsonify({"error": str(exc)}), 409
    except ChowkiStateError as exc:
        return jsonify({"error": str(exc)}), 404
    except HumanRejectedError:
        return jsonify({"outcome": "rejected"}), 200
    except WorkflowPaused as exc:
        return jsonify({"outcome": "paused", "token": exc.token, "step_id": exc.step_id}), 202
```

---

## Long Re-Executions: Background Tasks

Calling `resume()` or `aresume()` re-executes the workflow function body from the top down to the next boundary (or completion). If downstream steps perform long-running I/O or LLM calls, an inline HTTP request may time out.

For long re-executions, dispatch `aresume()` in a background worker and return `202 Accepted` immediately:

```python
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()


async def _background_resume(payload: ResumePayload) -> None:
    # Execute resumption in background task worker
    try:
        await chowki.aresume(
            run_id=payload.run_id,
            token=payload.token,
            decision=Decision(payload.decision),
            patch=payload.patch,
            note=payload.note,
        )
    except Exception:
        pass  # Errors recorded in storage audit log and run status


@app.post("/api/v1/resume/async")
async def resume_workflow_async(payload: ResumePayload, background_tasks: BackgroundTasks):
    background_tasks.add_task(_background_resume, payload)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "run_id": payload.run_id,
            "message": "Resume task scheduled in background. Poll chowki.inspect_run() for status.",
        },
    )
```

Clients can poll state using `chowki.inspect_run(run_id)` or listen for webhooks/gateway callbacks.

---

## Security Best Practices

1. **Route Authentication:** The resume token is scope-bound and signed, but you should still require standard session/token authentication on your admin routes.
2. **Never Log Tokens:** Resume tokens carry HMAC credentials. Never write full tokens to application logs, telemetry, or query strings.
3. **Token Reissuance:** If a token expires or is lost, call `chowki.reissue_token(run_id)` to issue a fresh single-use token for the paused gate.
4. **Token Provenance:** Resume tokens originate from `WorkflowPaused.token`, gateway notification payloads, or CLI commands (`chowki reissue-token`).
5. **Permitted Actions Scoping:** `chowki.pause()` defaults to `permitted_actions=("APPROVE", "REJECT")`. If a caller sends `EDIT` or `ESCALATE` when the pause gate did not permit it, `InvalidResumeToken` is raised resulting in 401.

---

## Phase 4 Preview: Hosted REST Gateway

If you prefer `chowki` to manage approval endpoints, webhooks, Slack/Teams buttons, and signed callbacks directly, Phase 4 introduces the hosted REST Gateway service. For embedded web apps, the recipes in this guide remain the primary pattern.

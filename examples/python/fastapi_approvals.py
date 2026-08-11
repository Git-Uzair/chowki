"""FastAPI production resume endpoint example for chowki workflows.

This example demonstrates how to integrate chowki workflow approvals into your own app:
1. Inline resumption via `chowki.aresume(...)`
2. Background task resumption using FastAPI `BackgroundTasks` for long re-executions.

Note on permitted_actions:
    `chowki.pause()` defaults to `permitted_actions=("APPROVE", "REJECT")`. If a caller
    sends `EDIT` or `ESCALATE` when the pause gate did not permit it, `InvalidResumeToken`
    is raised resulting in HTTP 401.

To run this example:
    pip install fastapi uvicorn
    uvicorn examples.python.fastapi_approvals:app --reload
"""

from __future__ import annotations

from typing import Any

import chowki
from chowki.config import ChowkiEngine
from chowki.errors import (
    ChowkiStateError,
    ExpiredResumeToken,
    HumanRejectedError,
    InvalidResumeToken,
    ReplayedNonceError,
    WorkflowPaused,
)
from chowki.types import Decision

# Optional import guard for FastAPI & Pydantic so this module can be imported
# in environments without web framework dependencies.
try:
    import fastapi  # type: ignore[import-not-found, import-untyped]  # noqa: F401
    import pydantic  # type: ignore[import-not-found, import-untyped]  # noqa: F401

    has_fastapi = True
except ImportError:  # pragma: no cover
    has_fastapi = False

if has_fastapi:
    from fastapi import (  # type: ignore[import-not-found, import-untyped]
        BackgroundTasks,
        FastAPI,
        Response,
    )
    from fastapi.responses import JSONResponse  # type: ignore[import-not-found, import-untyped]
    from pydantic import BaseModel, Field  # type: ignore[import-not-found, import-untyped]

    class ResumeRequest(BaseModel):  # type: ignore[misc]
        run_id: str = Field(..., description="The ID of the paused workflow run")
        token: str = Field(..., description="Single-use HMAC-signed resume token")
        decision: str = Field(
            ...,
            description=(
                "APPROVE, REJECT, EDIT, or ESCALATE (Note: EDIT and ESCALATE require"
                " permitted_actions configured on chowki.pause())"
            ),
        )
        patch: list[dict[str, Any]] | None = Field(
            default=None, description="RFC 6902 JSON Patch operations for EDIT decision"
        )
        note: str | None = Field(
            default=None, description="Optional audit note or rejection reason"
        )

else:  # Fallback stub for environments without pydantic/fastapi

    class ResumeRequest:  # type: ignore[no-redef]
        def __init__(
            self,
            run_id: str,
            token: str,
            decision: str,
            patch: list[dict[str, Any]] | None = None,
            note: str | None = None,
        ) -> None:
            self.run_id = run_id
            self.token = token
            self.decision = decision
            self.patch = patch
            self.note = note

        def model_dump(self) -> dict[str, Any]:
            return {
                "run_id": self.run_id,
                "token": self.token,
                "decision": self.decision,
                "patch": self.patch,
                "note": self.note,
            }

        def dict(self) -> dict[str, Any]:
            return self.model_dump()


def process_resume(
    run_id: str,
    token: str,
    decision_str: str,
    patch: list[dict[str, Any]] | None = None,
    note: str | None = None,
    engine: ChowkiEngine | None = None,
) -> tuple[int, dict[str, Any]]:
    """Core synchronous resume logic mapping chowki outcomes and exceptions to HTTP status and body.

    Note on permitted_actions:
        chowki.pause() defaults to permitted_actions=("APPROVE", "REJECT").
        If a caller sends EDIT or ESCALATE when the pause gate did not permit it,
        InvalidResumeToken is raised resulting in 401.
    """
    try:
        decision = Decision(decision_str)
        res = chowki.resume(
            run_id=run_id,
            token=token,
            decision=decision,
            patch=patch,  # type: ignore[arg-type]
            note=note,
            engine=engine,
        )
        return 200, {"outcome": "completed", "value": res.value}
    except InvalidResumeToken as exc:
        return 401, {"error": str(exc)}
    except ExpiredResumeToken as exc:
        return 410, {"error": str(exc)}
    except ReplayedNonceError as exc:
        return 409, {"error": str(exc)}
    except ChowkiStateError as exc:
        return 404, {"error": str(exc)}
    except HumanRejectedError:
        return 200, {"outcome": "rejected"}
    except WorkflowPaused as exc:
        return 202, {"outcome": "paused", "token": exc.token, "step_id": exc.step_id}


async def aprocess_resume(
    run_id: str,
    token: str,
    decision_str: str,
    patch: list[dict[str, Any]] | None = None,
    note: str | None = None,
    engine: ChowkiEngine | None = None,
) -> tuple[int, dict[str, Any]]:
    """Core async resume logic mapping chowki outcomes and exceptions to HTTP status and body.

    Note on permitted_actions:
        chowki.pause() defaults to permitted_actions=("APPROVE", "REJECT").
        If a caller sends EDIT or ESCALATE when the pause gate did not permit it,
        InvalidResumeToken is raised resulting in 401.
    """
    try:
        decision = Decision(decision_str)
        res = await chowki.aresume(
            run_id=run_id,
            token=token,
            decision=decision,
            patch=patch,  # type: ignore[arg-type]
            note=note,
            engine=engine,
        )
        return 200, {"outcome": "completed", "value": res.value}
    except InvalidResumeToken as exc:
        return 401, {"error": str(exc)}
    except ExpiredResumeToken as exc:
        return 410, {"error": str(exc)}
    except ReplayedNonceError as exc:
        return 409, {"error": str(exc)}
    except ChowkiStateError as exc:
        return 404, {"error": str(exc)}
    except HumanRejectedError:
        return 200, {"outcome": "rejected"}
    except WorkflowPaused as exc:
        return 202, {"outcome": "paused", "token": exc.token, "step_id": exc.step_id}


if has_fastapi:  # pragma: no cover
    app = FastAPI(title="chowki Approval Gateway")

    @app.post("/api/v1/resume")
    async def resume_inline_endpoint(req: ResumeRequest) -> Response:
        """Inline approval handler: awaits workflow re-execution to completion/pause."""
        status_code, body = await aprocess_resume(
            run_id=req.run_id,
            token=req.token,
            decision_str=req.decision,
            patch=req.patch,
            note=req.note,
        )
        return JSONResponse(status_code=status_code, content=body)

    async def _bg_resume_worker(
        run_id: str,
        token: str,
        decision_str: str,
        patch: list[dict[str, Any]] | None,
        note: str | None,
    ) -> None:
        await aprocess_resume(run_id, token, decision_str, patch, note)

    @app.post("/api/v1/resume/async")
    async def resume_background_endpoint(
        req: ResumeRequest, background_tasks: BackgroundTasks
    ) -> Response:
        """Background task handler: dispatches re-execution asynchronously and returns 202."""
        background_tasks.add_task(
            _bg_resume_worker, req.run_id, req.token, req.decision, req.patch, req.note
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "run_id": req.run_id,
                "message": ("Resume task scheduled in background. Poll /inspect_run or webhook."),
            },
        )

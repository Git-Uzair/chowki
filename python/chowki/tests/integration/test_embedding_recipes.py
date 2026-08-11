"""Integration tests for web app embedding recipes and approval endpoints.

Tests the exception-to-HTTP mapping logic and validates the FastAPI approvals example module.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

import chowki
from chowki.config import ChowkiConfig, ChowkiEngine, reset_engine
from chowki.core.registry import register_workflow
from chowki.errors import (
    ChowkiStateError,
    ExpiredResumeToken,
    HumanRejectedError,
    InvalidResumeToken,
    ReplayedNonceError,
    WorkflowPaused,
)
from chowki.storage.memory import MemoryStorage
from chowki.types import Decision

# Add repo root to sys.path to import examples.python.fastapi_approvals
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.python.fastapi_approvals import ResumeRequest, process_resume  # noqa: E402


# Workflows for testing
@chowki.workflow
def sync_single_gate_workflow() -> str:
    res = chowki.pause(reason="Sync Gate 1", payload={"foo": "bar"})
    return f"sync_done:{res if res is not None else 'ok'}"


@chowki.workflow
def sync_double_gate_workflow() -> str:
    chowki.pause(reason="Sync Gate 1")
    chowki.pause(reason="Sync Gate 2")
    return "sync_double_done"


@chowki.workflow
async def async_single_gate_workflow() -> str:
    res = chowki.pause(reason="Async Gate 1", payload={"foo": "bar"})
    return f"async_done:{res if res is not None else 'ok'}"


@chowki.workflow
async def async_double_gate_workflow() -> str:
    chowki.pause(reason="Async Gate 1")
    chowki.pause(reason="Async Gate 2")
    return "async_double_done"


@pytest.fixture(autouse=True)
def register_test_workflows() -> None:
    register_workflow("sync_single_gate_workflow", sync_single_gate_workflow)
    register_workflow("sync_double_gate_workflow", sync_double_gate_workflow)
    register_workflow("async_single_gate_workflow", async_single_gate_workflow)
    register_workflow("async_double_gate_workflow", async_double_gate_workflow)


# Sync plain handler mapping recipe
def sync_resume_handler(
    payload: dict[str, Any], engine: ChowkiEngine | None = None
) -> tuple[int, dict[str, Any]]:
    try:
        decision = Decision(payload["decision"])
        res = chowki.resume(
            run_id=payload["run_id"],
            token=payload["token"],
            decision=decision,
            patch=payload.get("patch"),
            note=payload.get("note"),
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


# Async plain handler mapping recipe
async def async_resume_handler(
    payload: dict[str, Any], engine: ChowkiEngine | None = None
) -> tuple[int, dict[str, Any]]:
    try:
        decision = Decision(payload["decision"])
        res = await chowki.aresume(
            run_id=payload["run_id"],
            token=payload["token"],
            decision=decision,
            patch=payload.get("patch"),
            note=payload.get("note"),
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


@pytest.fixture
def mem_engine() -> Iterator[ChowkiEngine]:
    reset_engine()
    cfg = ChowkiConfig(
        resume_secret=b"test-secret-32-bytes-long-123456",
        storage=MemoryStorage(),
    )
    eng = ChowkiEngine(cfg)
    chowki.configure(
        resume_secret=b"test-secret-32-bytes-long-123456",
        storage=eng.storage,
    )
    yield eng
    reset_engine()


# --- Sync Handler Recipe Tests ---


def test_sync_mapping_invalid_token(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused):
        sync_single_gate_workflow(run_id=run_id)

    payload = {"run_id": run_id, "token": "invalid.token.string", "decision": "APPROVE"}
    code, body = sync_resume_handler(payload, engine=mem_engine)
    assert code == 401
    assert "error" in body


def test_sync_mapping_expired_token(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        sync_single_gate_workflow(run_id=run_id)

    paused_token = exc_info.value.token
    assert paused_token is not None

    expired_token = mem_engine.tokens.issue(
        run_id=run_id,
        step_id=exc_info.value.step_id,
        permitted_actions=("APPROVE", "REJECT"),
        ttl=-10,
    )

    payload = {"run_id": run_id, "token": expired_token, "decision": "APPROVE"}
    code, body = sync_resume_handler(payload, engine=mem_engine)
    assert code == 410
    assert "error" in body


def test_sync_mapping_replayed_nonce(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        sync_single_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "APPROVE"}
    code1, body1 = sync_resume_handler(payload, engine=mem_engine)
    assert code1 == 200
    assert body1 == {"outcome": "completed", "value": "sync_done:ok"}

    code2, body2 = sync_resume_handler(payload, engine=mem_engine)
    assert code2 == 409
    assert "error" in body2


def test_sync_mapping_state_error(mem_engine: ChowkiEngine) -> None:
    valid_token = mem_engine.tokens.issue(
        run_id="unknown_run",
        step_id="step_1",
        permitted_actions=("APPROVE", "REJECT"),
    )
    payload = {"run_id": "unknown_run", "token": valid_token, "decision": "APPROVE"}
    code, body = sync_resume_handler(payload, engine=mem_engine)
    assert code == 404
    assert "error" in body


def test_sync_mapping_human_rejected(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        sync_single_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "REJECT", "note": "Not authorized"}
    code, body = sync_resume_handler(payload, engine=mem_engine)
    assert code == 200
    assert body == {"outcome": "rejected"}


def test_sync_mapping_workflow_paused(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        sync_double_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "APPROVE"}
    code, body = sync_resume_handler(payload, engine=mem_engine)
    assert code == 202
    assert body["outcome"] == "paused"
    assert "token" in body
    assert body["token"] != token
    assert "step_id" in body


def test_sync_mapping_success(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        sync_single_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "APPROVE"}
    code, body = sync_resume_handler(payload, engine=mem_engine)
    assert code == 200
    assert body == {"outcome": "completed", "value": "sync_done:ok"}


# --- Async Handler Recipe Tests ---


@pytest.mark.asyncio
async def test_async_mapping_invalid_token(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused):
        await async_single_gate_workflow(run_id=run_id)

    payload = {"run_id": run_id, "token": "invalid.token.string", "decision": "APPROVE"}
    code, body = await async_resume_handler(payload, engine=mem_engine)
    assert code == 401
    assert "error" in body


@pytest.mark.asyncio
async def test_async_mapping_expired_token(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        await async_single_gate_workflow(run_id=run_id)

    paused_token = exc_info.value.token
    assert paused_token is not None

    expired_token = mem_engine.tokens.issue(
        run_id=run_id,
        step_id=exc_info.value.step_id,
        permitted_actions=("APPROVE", "REJECT"),
        ttl=-10,
    )

    payload = {"run_id": run_id, "token": expired_token, "decision": "APPROVE"}
    code, body = await async_resume_handler(payload, engine=mem_engine)
    assert code == 410
    assert "error" in body


@pytest.mark.asyncio
async def test_async_mapping_replayed_nonce(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        await async_single_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "APPROVE"}
    code1, body1 = await async_resume_handler(payload, engine=mem_engine)
    assert code1 == 200
    assert body1 == {"outcome": "completed", "value": "async_done:ok"}

    code2, body2 = await async_resume_handler(payload, engine=mem_engine)
    assert code2 == 409
    assert "error" in body2


@pytest.mark.asyncio
async def test_async_mapping_state_error(mem_engine: ChowkiEngine) -> None:
    valid_token = mem_engine.tokens.issue(
        run_id="unknown_run",
        step_id="step_1",
        permitted_actions=("APPROVE", "REJECT"),
    )
    payload = {"run_id": "unknown_run", "token": valid_token, "decision": "APPROVE"}
    code, body = await async_resume_handler(payload, engine=mem_engine)
    assert code == 404
    assert "error" in body


@pytest.mark.asyncio
async def test_async_mapping_human_rejected(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        await async_single_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "REJECT"}
    code, body = await async_resume_handler(payload, engine=mem_engine)
    assert code == 200
    assert body == {"outcome": "rejected"}


@pytest.mark.asyncio
async def test_async_mapping_workflow_paused(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        await async_double_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "APPROVE"}
    code, body = await async_resume_handler(payload, engine=mem_engine)
    assert code == 202
    assert body["outcome"] == "paused"
    assert "token" in body
    assert body["token"] != token
    assert "step_id" in body


@pytest.mark.asyncio
async def test_async_mapping_success(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        await async_single_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    payload = {"run_id": run_id, "token": token, "decision": "APPROVE"}
    code, body = await async_resume_handler(payload, engine=mem_engine)
    assert code == 200
    assert body == {"outcome": "completed", "value": "async_done:ok"}


# --- Example Module Import Check & Unit Test ---


@pytest.mark.asyncio
async def test_fastapi_approvals_example_module(mem_engine: ChowkiEngine) -> None:
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    with pytest.raises(WorkflowPaused) as exc_info:
        await async_single_gate_workflow(run_id=run_id)

    token = exc_info.value.token
    assert token is not None

    req = ResumeRequest(run_id=run_id, token=token, decision="APPROVE")
    req_dump: dict[str, Any] = req.model_dump() if hasattr(req, "model_dump") else req.dict()

    code, body = await process_resume(
        run_id=cast(str, req_dump["run_id"]),
        token=cast(str, req_dump["token"]),
        decision_str=cast(str, req_dump["decision"]),
        patch=cast(Any, req_dump.get("patch")),
        note=cast(Any, req_dump.get("note")),
        engine=mem_engine,
    )
    assert code == 200
    assert body == {"outcome": "completed", "value": "async_done:ok"}

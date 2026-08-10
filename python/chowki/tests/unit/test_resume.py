from __future__ import annotations

from typing import Any, cast

import pytest

from chowki.config import ChowkiEngine
from chowki.core.context import current_run
from chowki.core.decorators import step, workflow
from chowki.core.resume import ResumeResult, resume
from chowki.core.runner import pause
from chowki.errors import HumanRejectedError, WorkflowPaused
from chowki.types import Decision, JSONObject, RunStatus


def build(engine: ChowkiEngine, calls: list[str]):
    @step
    def prepare() -> JSONObject:
        calls.append("prepare")
        return {"recipient": "wrong@example.com", "amount": 5000}

    @workflow(engine=engine)
    def transfer() -> str:
        proposal = prepare()
        current_run().state["proposal"] = proposal
        pause(
            reason="approve transfer",
            payload=proposal,
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
        )
        calls.append("send")
        prop = cast(dict[str, Any], current_run().state["proposal"])
        return f"sent to {prop['recipient']}"

    return transfer


def test_approve_resumes_and_completes(engine: ChowkiEngine) -> None:
    calls: list[str] = []
    transfer = build(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r1")
    token = excinfo.value.token
    assert token is not None

    result = resume(
        run_id="r1", token=token, decision=Decision.APPROVE, workflow_fn=transfer, engine=engine
    )
    assert isinstance(result, ResumeResult)
    assert result.value == "sent to wrong@example.com"
    assert calls == ["prepare", "send"], "the completed step must not run twice"

    run = engine.storage.get_run("r1")
    assert run is not None and run.status is RunStatus.COMPLETED


def test_edit_applies_an_rfc6902_patch_before_resuming(engine: ChowkiEngine) -> None:
    calls: list[str] = []
    transfer = build(engine, calls)

    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r2")
    token = excinfo.value.token
    assert token is not None

    result = resume(
        run_id="r2",
        token=token,
        decision=Decision.EDIT,
        patch=[{"op": "replace", "path": "/proposal/recipient", "value": "verified@company.com"}],
        workflow_fn=transfer,
        engine=engine,
    )
    assert result.value == "sent to verified@company.com"
    assert calls == ["prepare", "send"]


def test_patch_test_op_guards_against_a_stale_edit(engine: ChowkiEngine) -> None:
    from chowki.errors import ChowkiStateError

    transfer = build(engine, [])
    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r3")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(ChowkiStateError):
        resume(
            run_id="r3",
            token=token,
            decision=Decision.EDIT,
            patch=[
                {"op": "test", "path": "/proposal/amount", "value": 999},
                {"op": "replace", "path": "/proposal/amount", "value": 1},
            ],
            workflow_fn=transfer,
            engine=engine,
        )


def test_reject_raises_and_marks_the_run_rejected(engine: ChowkiEngine) -> None:
    calls: list[str] = []
    transfer = build(engine, calls)
    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r4")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(HumanRejectedError):
        resume(
            run_id="r4",
            token=token,
            decision=Decision.REJECT,
            workflow_fn=transfer,
            engine=engine,
        )

    assert calls == ["prepare"], "the post-pause body must never run after a rejection"
    run = engine.storage.get_run("r4")
    assert run is not None and run.status is RunStatus.REJECTED


def test_a_token_cannot_be_replayed(engine: ChowkiEngine) -> None:
    from chowki.errors import ReplayedNonceError

    transfer = build(engine, [])
    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r5")
    token = excinfo.value.token
    assert token is not None

    resume(run_id="r5", token=token, decision=Decision.APPROVE, workflow_fn=transfer, engine=engine)
    with pytest.raises(ReplayedNonceError):
        resume(
            run_id="r5", token=token, decision=Decision.APPROVE, workflow_fn=transfer, engine=engine
        )


def test_a_token_for_another_run_is_rejected(engine: ChowkiEngine) -> None:
    from chowki.errors import InvalidResumeToken

    transfer = build(engine, [])
    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r6")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(InvalidResumeToken, match="run"):
        resume(
            run_id="OTHER",
            token=token,
            decision=Decision.APPROVE,
            workflow_fn=transfer,
            engine=engine,
        )


def test_resuming_a_run_that_is_not_paused_is_an_error(engine: ChowkiEngine) -> None:
    from chowki.errors import ChowkiStateError

    with pytest.raises(ChowkiStateError, match="not paused"):
        resume(
            run_id="ghost",
            token="x.y",
            decision=Decision.APPROVE,
            workflow_fn=lambda: None,
            engine=engine,
        )


def test_an_audit_record_is_written_with_the_hash_chain(engine: ChowkiEngine) -> None:
    transfer = build(engine, [])
    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r7")
    token = excinfo.value.token
    assert token is not None

    resume(
        run_id="r7",
        token=token,
        decision=Decision.EDIT,
        patch=[{"op": "replace", "path": "/proposal/amount", "value": 1}],
        workflow_fn=transfer,
        engine=engine,
        actor={"user_id": "U1"},
    )

    records = engine.storage.list_audit(run_id="r7")
    assert len(records) == 1
    rec = records[0]
    assert rec["action"] == "EDIT"
    orig_hash = rec["original_state_hash"]
    patched_hash = rec["patched_state_hash"]
    assert isinstance(orig_hash, str) and orig_hash.startswith("sha256:")
    assert isinstance(patched_hash, str) and patched_hash.startswith("sha256:")
    assert orig_hash != patched_hash
    assert rec["actor"] == {"user_id": "U1"}

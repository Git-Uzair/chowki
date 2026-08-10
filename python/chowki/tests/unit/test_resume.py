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


def test_finding1_fast_patch_does_not_mutate_base_on_fallback() -> None:
    from chowki.state.delta import apply_patch

    base = {"items": [1, 2]}
    patch = [
        {"op": "add", "path": "/items/-", "value": 3},
        {"op": "move", "from": "/items", "path": "/moved"},
    ]
    res = apply_patch(base, patch, in_place=True)
    assert res == {"moved": [1, 2, 3]}


def test_finding2_materialize_handles_complex_ops_in_delta_chain() -> None:
    from chowki.state.delta import DeltaChain

    chain = DeltaChain(base={"x": 1})
    chain.append([{"op": "add", "path": "/y", "value": 2}])
    chain.append([{"op": "move", "from": "/y", "path": "/z"}])
    res = chain.materialize()
    assert res == {"x": 1, "z": 2}


def test_finding3_copy_containers_isolates_nested_dicts_in_lists() -> None:
    from chowki.state.pipeline import _copy_containers  # pyright: ignore[reportPrivateUsage]

    val = {"items": [{"id": 1}]}
    copied = _copy_containers(val)
    copied["items"][0]["id"] = 2
    assert val["items"][0]["id"] == 1


def test_finding4_inline_blobs_decodes_escaped_literals_across_restart() -> None:
    from chowki.state.blobs import BlobStore
    from chowki.state.pipeline import SnapshotPipeline
    from chowki.state.redact import Redactor

    pipe1 = SnapshotPipeline(redactor=Redactor(hmac_key=b"k"), blobs=BlobStore())
    env = pipe1.snapshot({"key": "ref-lit:hello"}, run_id="r_f4", workflow="w", step_index=0)

    pipe2 = SnapshotPipeline(redactor=Redactor(hmac_key=b"k"), blobs=BlobStore())
    restored = pipe2.load([env])
    assert restored == {"key": "ref-lit:hello"}


def test_finding5_type_error_inside_workflow_is_not_masked(engine: ChowkiEngine) -> None:
    calls: list[str] = []

    @workflow(engine=engine)
    def broken_wf() -> None:
        calls.append("run")
        pause(reason="pause")
        raise TypeError("internal error inside workflow")

    with pytest.raises(WorkflowPaused) as excinfo:
        broken_wf(run_id="r_f5")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(TypeError, match="internal error inside workflow"):
        resume(
            run_id="r_f5",
            token=token,
            decision=Decision.APPROVE,
            workflow_fn=broken_wf,
            engine=engine,
        )
    assert calls == ["run", "run"]


def test_finding6_escalate_decision_replaces_pause_struct(engine: ChowkiEngine) -> None:
    @workflow(engine=engine)
    def wf_f6() -> None:
        pause(reason="p", permitted_actions=("APPROVE", "ESCALATE"))

    with pytest.raises(WorkflowPaused) as excinfo:
        wf_f6(run_id="r_f6")
    token = excinfo.value.token
    assert token is not None

    with pytest.raises(WorkflowPaused) as exc_esc:
        resume(
            run_id="r_f6",
            token=token,
            decision=Decision.ESCALATE,
            workflow_fn=wf_f6,
            engine=engine,
            actor={"reviewers": ["manager1"]},
        )
    assert exc_esc.value.token is not None
    run = engine.storage.get_run("r_f6")
    assert run is not None and run.pause is not None
    assert run.pause.reviewers == ("manager1",)


def test_finding7_two_gate_workflow_resumes_through_both_gates(engine: ChowkiEngine) -> None:
    calls: list[str] = []

    @step
    def do_start() -> None:
        calls.append("start")

    @step
    def do_middle() -> None:
        calls.append("middle")

    @workflow(engine=engine)
    def two_gates() -> str:
        do_start()
        pause(reason="gate1")
        do_middle()
        pause(reason="gate2")
        calls.append("end")
        return "done"

    with pytest.raises(WorkflowPaused) as exc1:
        two_gates(run_id="r_f7")

    token1 = exc1.value.token
    assert token1 is not None

    with pytest.raises(WorkflowPaused) as exc2:
        resume(
            run_id="r_f7",
            token=token1,
            decision=Decision.APPROVE,
            workflow_fn=two_gates,
            engine=engine,
        )

    token2 = exc2.value.token
    assert token2 is not None

    res = resume(
        run_id="r_f7",
        token=token2,
        decision=Decision.APPROVE,
        workflow_fn=two_gates,
        engine=engine,
    )
    assert res.value == "done"
    assert calls == ["start", "middle", "end"]


def test_finding8_state_dict_updates_during_resume() -> None:
    from chowki.core.context import StateDict

    sd = StateDict({"prop": {"a": 1}}, frozen_keys={"prop"})
    sd["prop"] = {"a": 1, "b": 2}
    assert sd["prop"] == {"a": 1, "b": 2}
    sd["new_key"] = "x"
    assert sd["new_key"] == "x"


def test_finding9_malformed_patch_elements_raise_chowki_state_error() -> None:
    from chowki.errors import ChowkiStateError
    from chowki.state.delta import apply_patch

    with pytest.raises(ChowkiStateError):
        apply_patch({"a": 1}, [123])  # type: ignore[list-item]


def test_finding10_core_resume_docstrings() -> None:
    import chowki.core.resume as resume_mod

    assert resume_mod.__doc__ is not None
    assert "@chowki.step" in resume_mod.__doc__
    assert resume_mod.resume.__doc__ is not None
    assert "@chowki.step" in resume_mod.resume.__doc__
    assert "Phase 2" in resume_mod.resume.__doc__

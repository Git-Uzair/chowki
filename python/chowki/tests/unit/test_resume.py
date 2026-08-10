from __future__ import annotations

import copy
from typing import Any, cast

import pytest

from chowki.config import ChowkiEngine
from chowki.core.context import current_run
from chowki.core.decorators import step, workflow
from chowki.core.resume import ResumeResult, resume
from chowki.core.runner import pause
from chowki.errors import HumanRejectedError, WorkflowPaused
from chowki.types import Decision, JSONObject, JSONValue, RunStatus


def probe_state_of_record(engine: ChowkiEngine, run_id: str) -> JSONValue:
    """Reconstruct a run's persisted state without disturbing its live pipeline."""
    from chowki.state.pipeline import SnapshotPipeline

    probe = SnapshotPipeline(
        redactor=engine.redactor, blobs=engine.blobs, tenant_id=engine.config.tenant_id
    )
    return probe.load(engine.storage.snapshots_for_resume(run_id))


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


@pytest.mark.parametrize(
    ("base", "patch"),
    [
        ({"a": 1, "b": 2}, [{"op": "remove", "path": "/a"}, {"op": "remove", "path": "/a"}]),
        (
            {"d": {"x": 1, "y": 2}},
            [{"op": "remove", "path": "/d/x"}, {"op": "remove", "path": "/d/x"}],
        ),
        (
            {"items": [1, 2, 3]},
            [{"op": "remove", "path": "/items/0"}, {"op": "add", "path": "/items/3", "value": 9}],
        ),
    ],
)
def test_sequentially_conflicting_ops_are_rejected(
    base: dict[str, Any], patch: list[dict[str, Any]]
) -> None:
    """RFC 6902 is sequential: op N's preconditions hold against the result of op N-1."""
    from chowki.errors import ChowkiStateError
    from chowki.state.delta import apply_patch

    before = copy.deepcopy(base)
    with pytest.raises(ChowkiStateError):
        apply_patch(base, patch)
    assert base == before, "a rejected patch must leave the base untouched"


def test_sequentially_valid_ops_are_applied_in_order() -> None:
    from chowki.state.delta import apply_patch

    base = {"items": [1, 2, 3], "a": 1}
    res = apply_patch(
        base,
        [
            {"op": "remove", "path": "/items/0"},
            {"op": "add", "path": "/items/2", "value": 9},
            {"op": "remove", "path": "/a"},
        ],
    )
    assert res == {"items": [2, 3, 9]}
    assert base == {"items": [1, 2, 3], "a": 1}


def test_materialize_does_not_reapply_ops_that_precede_a_complex_op() -> None:
    """A patch whose simple prefix is applied before a fallback op must not double-apply."""
    from chowki.state.delta import DeltaChain

    chain = DeltaChain(base={"items": [1, 2]})
    chain.append([{"op": "add", "path": "/items/-", "value": 3}])
    chain.append(
        [
            {"op": "add", "path": "/items/-", "value": 4},
            {"op": "move", "from": "/items", "path": "/moved"},
        ]
    )
    assert chain.materialize() == {"moved": [1, 2, 3, 4]}
    assert chain.base == {"items": [1, 2]}


def build_observed(engine: ChowkiEngine, observed: list[JSONObject]) -> Any:
    @step
    def prepare() -> JSONObject:
        return {"recipient": "wrong@example.com", "amount": 5000}

    @workflow(engine=engine)
    def transfer() -> str:
        current_run().state["proposal"] = prepare()
        pause(
            reason="approve transfer",
            payload={},
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
        )
        observed.append(copy.deepcopy(current_run().state))
        return "sent"

    return transfer


def test_an_edit_that_removes_a_key_is_visible_to_the_resumed_body(
    engine: ChowkiEngine,
) -> None:
    from chowki.state.canonical import content_hash

    observed: list[JSONObject] = []
    transfer = build_observed(engine, observed)

    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r_rm")
    token = excinfo.value.token
    assert token is not None

    resume(
        run_id="r_rm",
        token=token,
        decision=Decision.EDIT,
        patch=[{"op": "remove", "path": "/proposal/amount"}],
        workflow_fn=transfer,
        engine=engine,
    )

    assert observed[-1] == {"proposal": {"recipient": "wrong@example.com"}}
    rec = engine.storage.list_audit(run_id="r_rm")[0]
    assert rec["patched_state_hash"] == content_hash(observed[-1])
    assert probe_state_of_record(engine, "r_rm") == {"proposal": {"recipient": "wrong@example.com"}}


def test_an_edit_that_replaces_a_subtree_is_visible_to_the_resumed_body(
    engine: ChowkiEngine,
) -> None:
    observed: list[JSONObject] = []
    transfer = build_observed(engine, observed)

    with pytest.raises(WorkflowPaused) as excinfo:
        transfer(run_id="r_sub")
    token = excinfo.value.token
    assert token is not None

    resume(
        run_id="r_sub",
        token=token,
        decision=Decision.EDIT,
        patch=[{"op": "replace", "path": "/proposal", "value": {"recipient": "ok@x.com"}}],
        workflow_fn=transfer,
        engine=engine,
    )

    assert observed[-1] == {"proposal": {"recipient": "ok@x.com"}}


def test_replayed_pre_pause_writes_are_not_discarded(engine: ChowkiEngine) -> None:
    """The human's edit wins, but every other replayed write still takes effect."""
    observed: list[JSONObject] = []
    passes: list[int] = []

    @workflow(engine=engine)
    def wf() -> str:
        passes.append(1)
        state = current_run().state
        state["attempt"] = len(passes)
        state["proposal"] = {"amount": 100}
        pause(reason="gate", permitted_actions=("APPROVE", "REJECT", "EDIT"))
        observed.append(copy.deepcopy(state))
        return "done"

    with pytest.raises(WorkflowPaused) as excinfo:
        wf(run_id="r_replay")
    token = excinfo.value.token
    assert token is not None

    resume(
        run_id="r_replay",
        token=token,
        decision=Decision.EDIT,
        patch=[{"op": "remove", "path": "/proposal/amount"}],
        workflow_fn=wf,
        engine=engine,
    )

    assert observed[-1] == {"attempt": 2, "proposal": {}}


def test_a_human_edit_survives_a_later_gate(engine: ChowkiEngine) -> None:
    observed: list[JSONObject] = []

    @step
    def make_proposal() -> JSONObject:
        return {"amount": 100}

    @workflow(engine=engine)
    def two_gates() -> str:
        current_run().state["proposal"] = make_proposal()
        pause(reason="gate1", permitted_actions=("APPROVE", "REJECT", "EDIT"))
        current_run().state["mid"] = "mid"
        pause(reason="gate2", permitted_actions=("APPROVE", "REJECT"))
        observed.append(copy.deepcopy(current_run().state))
        return "done"

    with pytest.raises(WorkflowPaused) as exc1:
        two_gates(run_id="r_2g")
    token1 = exc1.value.token
    assert token1 is not None

    with pytest.raises(WorkflowPaused) as exc2:
        resume(
            run_id="r_2g",
            token=token1,
            decision=Decision.EDIT,
            patch=[{"op": "replace", "path": "/proposal/amount", "value": 7}],
            workflow_fn=two_gates,
            engine=engine,
        )
    token2 = exc2.value.token
    assert token2 is not None

    # The decision is the run's state of record from here on, not a process-local overlay.
    assert probe_state_of_record(engine, "r_2g") == {"proposal": {"amount": 7}, "mid": "mid"}

    res = resume(
        run_id="r_2g",
        token=token2,
        decision=Decision.APPROVE,
        workflow_fn=two_gates,
        engine=engine,
    )
    assert res.value == "done"
    assert observed[-1] == {"proposal": {"amount": 7}, "mid": "mid"}
    assert probe_state_of_record(engine, "r_2g") == {"proposal": {"amount": 7}, "mid": "mid"}

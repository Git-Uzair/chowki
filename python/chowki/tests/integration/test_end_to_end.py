"""The whole Phase 1 promise in one run, against a real SQLite file."""

from __future__ import annotations

import base64
import functools
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import chowki
from chowki.config import ChowkiConfig, ChowkiEngine
from chowki.core.context import current_run
from chowki.errors import ReplayedNonceError
from chowki.hitl.gateway import InMemoryGateway
from chowki.storage.sqlite import SQLiteStorage
from chowki.types import Decision, JSONObject, RunStatus

pytestmark = pytest.mark.integration

SECRET_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0"


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ChowkiEngine]:
    monkeypatch.setenv("CHOWKI_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    eng = ChowkiEngine(
        ChowkiConfig(
            storage=SQLiteStorage(tmp_path / "chowki.db"),
            encrypt_at_rest=True,
            gateway=InMemoryGateway(),
            resume_secret=b"a-stable-secret-for-this-test!!!",
        )
    )
    yield eng
    eng.close()


def test_full_lifecycle(engine: ChowkiEngine, tmp_path: Path) -> None:
    llm_calls: list[str] = []
    side_effects: list[str] = []

    @chowki.step
    def plan(goal: str) -> JSONObject:
        llm_calls.append("plan")
        return {"recipient": "typo@exmaple.com", "amount": 5000, "api_key": SECRET_KEY}

    @chowki.step
    def transfer(proposal: JSONObject) -> str:
        side_effects.append("transfer")
        return f"sent {proposal['amount']} to {proposal['recipient']}"

    @chowki.workflow(engine=engine)
    def payout(goal: str) -> str:
        proposal = plan(goal)
        current_run().state["proposal"] = proposal
        chowki.report_usage(chowki.Usage(input_tokens=1200, output_tokens=300, cost_usd=0.02))
        chowki.pause(
            reason="approve the payout",
            payload=proposal,
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
        )
        prop = current_run().state["proposal"]
        assert isinstance(prop, dict)
        return transfer(prop)

    # 1. Run until the human boundary.
    with pytest.raises(chowki.WorkflowPaused) as excinfo:
        payout("pay the vendor", run_id="e2e")
    token = excinfo.value.token
    assert token is not None
    assert llm_calls == ["plan"]
    assert side_effects == []

    # 2. The run is durable, paused, and its usage was recorded.
    run = engine.storage.get_run("e2e")
    assert run is not None
    assert run.status is RunStatus.PAUSED
    assert run.usage.billable_tokens == 1500

    # 3. Nothing on disk contains the credential, encrypted or not.
    db_bytes = b"".join(f.read_bytes() for f in tmp_path.glob("chowki.db*"))
    assert b"e2e" in db_bytes  # Positive control: confirms db WAL payload is checked
    assert SECRET_KEY.encode() not in db_bytes

    # 4. Snapshots are encrypted at rest.
    envelopes = engine.storage.list_snapshots("e2e")
    assert envelopes
    assert all(e.key_id == "k1" and e.nonce is not None for e in envelopes)

    # 5. The reviewer was notified through the gateway.
    gateway = engine.gateway
    assert isinstance(gateway, InMemoryGateway)
    assert len(gateway.notices) == 1

    # 6. A human fixes the typo and approves.
    result = chowki.resume(
        run_id="e2e",
        token=token,
        decision=Decision.EDIT,
        patch=[
            {"op": "test", "path": "/proposal/amount", "value": 5000},
            {"op": "replace", "path": "/proposal/recipient", "value": "vendor@example.com"},
        ],
        workflow_fn=functools.partial(payout, "pay the vendor"),
        engine=engine,
        actor={"platform": "web", "user_id": "U1"},
    )

    # 7. Zero-waste: the LLM step never re-ran; the side effect ran exactly once.
    assert result.value == "sent 5000 to vendor@example.com"
    assert llm_calls == ["plan"]
    assert side_effects == ["transfer"]

    # 8. The run completed and the audit trail is intact.
    run = engine.storage.get_run("e2e")
    assert run is not None and run.status is RunStatus.COMPLETED
    audit = engine.storage.list_audit(run_id="e2e")
    assert len(audit) == 1
    assert audit[0]["action"] == "EDIT"
    assert audit[0]["original_state_hash"] != audit[0]["patched_state_hash"]

    # 9. The token cannot be replayed.
    with pytest.raises(ReplayedNonceError):
        chowki.resume(
            run_id="e2e",
            token=token,
            decision=Decision.APPROVE,
            workflow_fn=functools.partial(payout, "pay the vendor"),
            engine=engine,
        )


def test_resume_across_engine_instances_restores_blob_extracted_state(tmp_path: Path) -> None:
    """A string over the blob threshold must survive a restart: pause in one
    engine, resume in a second engine on the same SQLite file. Blobs extracted
    from state have to be as durable as the snapshots that reference them."""
    db = tmp_path / "chowki.db"
    big_doc = "D" * 6000  # over the 4096-byte default blob threshold

    def build(eng: ChowkiEngine) -> Callable[..., str]:
        @chowki.step
        def load_doc() -> str:
            return big_doc

        @chowki.workflow(engine=eng, name="doc_review")
        def doc_review() -> str:
            ctx = current_run()
            ctx.state["doc"] = load_doc()
            chowki.pause(reason="review the document")
            doc = ctx.state["doc"]
            assert isinstance(doc, str)
            return f"approved:{len(doc)}"

        return doc_review

    eng1 = ChowkiEngine(ChowkiConfig(storage=SQLiteStorage(db), resume_secret=b"s" * 32))
    with pytest.raises(chowki.WorkflowPaused) as excinfo:
        build(eng1)(run_id="blob_run")
    token = excinfo.value.token
    assert token is not None
    eng1.close()

    eng2 = ChowkiEngine(ChowkiConfig(storage=SQLiteStorage(db), resume_secret=b"s" * 32))
    result = chowki.resume(
        run_id="blob_run",
        token=token,
        decision=Decision.APPROVE,
        workflow_fn=build(eng2),
        engine=eng2,
    )
    assert result.value == "approved:6000"
    eng2.close()


def test_crash_recovery_across_engine_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new process finds the incomplete run and resumes without repeating work."""
    monkeypatch.setenv("CHOWKI_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    db = tmp_path / "chowki.db"
    calls: list[str] = []

    @chowki.step
    def first() -> str:
        calls.append("first")
        return "one"

    def build(eng: ChowkiEngine) -> Callable[..., str]:
        @chowki.workflow(engine=eng, name="job")
        def job() -> str:
            a = first()
            if not calls.count("crashed"):
                calls.append("crashed")
                raise RuntimeError("process died")
            return a + "-two"

        return job

    eng1 = ChowkiEngine(ChowkiConfig(storage=SQLiteStorage(db)))

    # Simulate a process crash (SIGKILL/power loss) where _close_run never gets called
    def _noop_close(ctx: object, rec: object, exc: object) -> None:
        pass

    monkeypatch.setattr("chowki.core.runner._close_run", _noop_close)
    with pytest.raises(RuntimeError):
        build(eng1)(run_id="crash")
    eng1.close()

    eng2 = ChowkiEngine(ChowkiConfig(storage=SQLiteStorage(db)))
    pending = chowki.recover_runs(eng2)
    assert [r.run_id for r in pending] == ["crash"]

    assert build(eng2)(run_id="crash") == "one-two"
    assert calls.count("first") == 1, "the completed step must not re-execute"
    eng2.close()

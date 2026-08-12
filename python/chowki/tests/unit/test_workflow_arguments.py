"""Workflow arguments are part of the run record, so a resume replays the real call.

Before this, `resume()` called `workflow_fn(run_id=run_id)` and nothing else: a required
parameter made the run unresumable, and a defaulted parameter silently bound the default
on resume, missing every args_hash and re-running steps against the wrong entity
(POSITIONING.md:293-414).
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from structlog.testing import capture_logs

import chowki
from chowki.config import ChowkiEngine
from chowki.errors import ChowkiStateError
from chowki.state.codec import decode_state
from chowki.types import RunRecord, RunStatus


def _resume(engine: ChowkiEngine, run_id: str, token: str, fn: Any) -> Any:
    return chowki.resume(
        run_id=run_id,
        token=token,
        decision=chowki.Decision.APPROVE,
        workflow_fn=fn,
        engine=engine,
    )


def test_a_required_argument_survives_the_pause(engine: ChowkiEngine) -> None:
    fetched: list[str] = []

    @chowki.step
    def fetch_invoice(invoice_id: str) -> str:
        fetched.append(invoice_id)
        return invoice_id

    @chowki.workflow(engine=engine)
    def billing_agent(invoice_id: str) -> str:
        found = fetch_invoice(invoice_id)
        chowki.pause(reason="payment needs a human")
        return f"paid {found}"

    with pytest.raises(chowki.WorkflowPaused) as paused:
        billing_agent("inv-999", run_id="run-req")
    token = paused.value.token
    assert token is not None

    result = _resume(engine, "run-req", token, billing_agent)

    assert result.value == "paid inv-999"
    assert fetched == ["inv-999"], "the step must be memoised, not re-run with a default"


def test_a_default_no_longer_shadows_the_real_argument(engine: ChowkiEngine) -> None:
    """The documented workaround's silent-failure mode: resume used to bind 'inv-1'."""
    fetched: list[str] = []

    @chowki.step
    def fetch_invoice(invoice_id: str) -> str:
        fetched.append(invoice_id)
        return invoice_id

    @chowki.workflow(engine=engine)
    def billing_agent(invoice_id: str = "inv-1") -> str:
        found = fetch_invoice(invoice_id)
        chowki.pause(reason="payment needs a human")
        return f"paid {found}"

    with pytest.raises(chowki.WorkflowPaused) as paused:
        billing_agent(invoice_id="inv-999", run_id="run-default")
    token = paused.value.token
    assert token is not None

    result = _resume(engine, "run-default", token, billing_agent)

    assert result.value == "paid inv-999"
    assert fetched == ["inv-999"]


def test_positional_and_keyword_arguments_both_replay(engine: ChowkiEngine) -> None:
    seen: list[tuple[str, int]] = []

    @chowki.workflow(engine=engine)
    def mixed(name: str, count: int = 0) -> str:
        seen.append((name, count))
        return name

    mixed("alpha", count=3, run_id="run-mixed")
    chowki.rerun("run-mixed", engine=engine)

    assert seen == [("alpha", 3), ("alpha", 3)]


def test_arguments_are_redacted_before_they_are_persisted(engine: ChowkiEngine) -> None:
    @chowki.workflow(engine=engine)
    def with_secret(api_key: str = "") -> str:
        return "done"

    with capture_logs() as logs:
        with_secret(api_key="sk-" + "A1b2C3d4E5f6G7h8I9j0" * 2, run_id="run-secret")

    record = engine.storage.get_run("run-secret")
    assert record is not None and record.inputs is not None
    decoded = cast(dict[str, Any], decode_state(record.inputs))
    assert "[REDACTED:" in decoded["kwargs"]["api_key"]
    assert any(entry["event"] == "chowki_workflow_args_redacted" for entry in logs)


def test_unencodable_arguments_are_not_persisted_but_do_not_break_the_run(
    engine: ChowkiEngine,
) -> None:
    @chowki.workflow(engine=engine)
    def takes_anything(payload: object = None) -> str:
        return "done"

    with capture_logs() as logs:
        assert takes_anything(object(), run_id="run-unenc") == "done"

    record = engine.storage.get_run("run-unenc")
    assert record is not None and record.inputs is None
    assert any(entry["event"] == "chowki_workflow_args_not_persisted" for entry in logs)


def test_a_run_without_stored_arguments_fails_loudly_not_with_a_typeerror(
    engine: ChowkiEngine,
) -> None:
    """A run written by an older chowki has no inputs; say so instead of raising TypeError."""

    @chowki.workflow(engine=engine)
    def legacy(invoice_id: str) -> str:
        return invoice_id

    engine.storage.put_run(
        RunRecord(
            run_id="run-legacy",
            # The name the decorator registered it under: rerun() resolves the target
            # through the registry, which is the only reference this workflow needs.
            workflow=legacy.__name__,
            tenant_id="default",
            created_at_utc="2026-08-12T00:00:00Z",
            updated_at_utc="2026-08-12T00:00:00Z",
            status=RunStatus.PENDING,
        )
    )

    with pytest.raises(ChowkiStateError, match="argument"):
        chowki.rerun("run-legacy", engine=engine)


def test_the_first_call_owns_the_stored_arguments(engine: ChowkiEngine) -> None:
    """Re-invoking a run id is a warm resume, not a new call: the record must not move."""

    @chowki.workflow(engine=engine)
    def pipeline(tag: str = "first") -> str:
        return tag

    pipeline("first", run_id="run-own")
    pipeline("second", run_id="run-own")

    record = engine.storage.get_run("run-own")
    assert record is not None and record.inputs is not None
    decoded = cast(dict[str, Any], decode_state(record.inputs))
    assert decoded["args"] == ["first"]

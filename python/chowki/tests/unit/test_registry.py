from __future__ import annotations

import pytest
from structlog.testing import capture_logs

import chowki
from chowki.config import ChowkiEngine
from chowki.core.registry import (
    clear_registry,
    get_workflow,
    register_workflow,
    registered_workflows,
)
from chowki.errors import ChowkiConfigError, ChowkiStateError
from chowki.types import RunRecord


def test_registry_basic_operations() -> None:
    def fn_a() -> str:
        return "a"

    register_workflow("fn_a", fn_a)
    assert get_workflow("fn_a") is fn_a
    assert get_workflow("unknown") is None

    reg = registered_workflows()
    assert reg == {"fn_a": fn_a}
    reg["fn_a"] = lambda: "modified"
    assert get_workflow("fn_a") is fn_a

    clear_registry()
    assert get_workflow("fn_a") is None
    assert registered_workflows() == {}


def test_registry_redefinition_warning() -> None:
    def fn1() -> str:
        return "v1"

    def fn2() -> str:
        return "v2"

    register_workflow("test_wf", fn1)

    # Re-registering exact same function does not log warning
    with capture_logs() as cap1:
        register_workflow("test_wf", fn1)
    assert not any(log.get("event") == "chowki_workflow_reregistered" for log in cap1)

    # Re-registering different function logs warning and replaces
    with capture_logs() as cap2:
        register_workflow("test_wf", fn2)

    assert get_workflow("test_wf") is fn2
    warn_logs = [log for log in cap2 if log.get("event") == "chowki_workflow_reregistered"]
    assert len(warn_logs) == 1
    assert warn_logs[0]["name"] == "test_wf"


def test_workflow_decorator_registration() -> None:
    @chowki.workflow
    def auto_reg_wf() -> str:
        return "auto"

    assert get_workflow("auto_reg_wf") is auto_reg_wf

    @chowki.workflow(name="custom_wf")
    def named_wf() -> str:
        return "named"

    assert get_workflow("custom_wf") is named_wf

    @chowki.workflow(register=False)
    def opt_out_wf() -> str:
        return "opt_out"

    assert get_workflow("opt_out_wf") is None
    _ = opt_out_wf


def test_resume_without_workflow_fn(engine: ChowkiEngine) -> None:
    @chowki.workflow(engine=engine)
    def my_pausing_wf() -> str:
        chowki.pause(reason="review")
        return "approved"

    with pytest.raises(chowki.WorkflowPaused) as exc_info:
        my_pausing_wf(run_id="run_resume_no_fn")

    assert exc_info.value.token is not None
    result = chowki.resume(
        run_id="run_resume_no_fn",
        token=exc_info.value.token,
        decision=chowki.Decision.APPROVE,
        engine=engine,
    )
    assert result.value == "approved"


def test_resume_with_explicit_workflow_name(engine: ChowkiEngine) -> None:
    @chowki.workflow(name="custom_registered_name", engine=engine)
    def explicit_name_wf() -> str:
        chowki.pause(reason="review")
        return "done"

    with pytest.raises(chowki.WorkflowPaused) as exc_info:
        explicit_name_wf(run_id="run_resume_exp_name")

    assert exc_info.value.token is not None
    result = chowki.resume(
        run_id="run_resume_exp_name",
        token=exc_info.value.token,
        decision=chowki.Decision.APPROVE,
        workflow="custom_registered_name",
        engine=engine,
    )
    assert result.value == "done"


def test_resume_unresolvable_workflow_raises_config_error(engine: ChowkiEngine) -> None:
    @chowki.workflow(register=False, engine=engine)
    def unregistered_wf() -> str:
        chowki.pause(reason="review")
        return "done"

    with pytest.raises(chowki.WorkflowPaused) as exc_info:
        unregistered_wf(run_id="run_unres_wf")

    token = exc_info.value.token
    assert token is not None
    with pytest.raises(ChowkiConfigError) as err_info:
        chowki.resume(
            run_id="run_unres_wf",
            token=token,
            decision=chowki.Decision.APPROVE,
            engine=engine,
        )
    assert "not found in registry" in str(err_info.value)

    # State and token must NOT be mutated on workflow resolution failure
    run = engine.storage.get_run("run_unres_wf")
    assert run is not None and run.status is chowki.RunStatus.PAUSED
    assert len(engine.storage.list_audit(run_id="run_unres_wf")) == 0

    # Token/nonce remains unconsumed, run stays PAUSED and resumable
    res = chowki.resume(
        run_id="run_unres_wf",
        token=token,
        decision=chowki.Decision.APPROVE,
        workflow_fn=unregistered_wf,
        engine=engine,
    )
    assert res.value == "done"


def test_rerun_completes_recovered_pending_run(engine: ChowkiEngine) -> None:
    step_calls = 0

    @chowki.step
    def count_step() -> int:
        nonlocal step_calls
        step_calls += 1
        return 42

    @chowki.workflow(engine=engine)
    def rerun_test_wf() -> int:
        val = count_step()
        return val * 2

    # Run workflow once so step is memoised
    rerun_test_wf(run_id="run_rerun_test")
    assert step_calls == 1

    # Simulate run reset to PENDING (e.g. after crash / recover_runs)
    run_rec = engine.storage.get_run("run_rerun_test")
    assert run_rec is not None
    run_rec.status = chowki.RunStatus.PENDING
    engine.storage.put_run(run_rec)

    # Call rerun - memoised step should NOT re-execute
    val = chowki.rerun("run_rerun_test", engine=engine)
    assert val == 84
    assert step_calls == 1, "Memoised step must not re-execute on rerun"


def test_rerun_unregistered_or_missing_run(engine: ChowkiEngine) -> None:
    with pytest.raises(ChowkiStateError):
        chowki.rerun("non_existent_run", engine=engine)

    # Create run record with unregistered workflow name
    rec = RunRecord(
        run_id="run_unreg_rerun",
        workflow="non_existent_wf",
        tenant_id="default",
        created_at_utc="2026-01-01T00:00:00Z",
        updated_at_utc="2026-01-01T00:00:00Z",
        status=chowki.RunStatus.PENDING,
    )
    engine.storage.put_run(rec)

    with pytest.raises(ChowkiConfigError) as err_info:
        chowki.rerun("run_unreg_rerun", engine=engine)
    assert "non_existent_wf" in str(err_info.value)

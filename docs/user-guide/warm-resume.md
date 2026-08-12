# Warm Resume & Durable Execution

`chowki` achieves durable execution without custom DSLs, state machines, or bytecode manipulation. It works by re-executing the workflow function from the top down and skipping steps whose results were previously recorded in storage.

---

## Re-Execution and Memoisation

When a paused, interrupted, or recovered workflow is resumed via `chowki.resume()` or `chowki.rerun()`, `chowki` invokes the workflow function from line 1.

As execution encounters each `@chowki.step`:
1. `chowki` computes the step's identity (idempotency key based on step name, ordinal position, and arguments hash).
2. If storage contains a completed `StepRecord` with a matching idempotency key, `chowki` skips function execution and returns the stored result immediately.
3. If no completed record exists, the step function executes, its result is written to disk, and workflow execution proceeds to the next line.

### Workflow Arguments Are Replayed From the Run Record

The arguments of the call that *started* a run are persisted on its run record (redacted, as MessagePack `{"args": [...], "kwargs": {...}}`) before the first step runs, and `resume()`, `aresume()` and `rerun()` replay them: `workflow_fn(*args, run_id=run_id, **kwargs)`. A required parameter is therefore fine — `bill("inv-999", run_id="r1")` resumes as `bill("inv-999", run_id="r1")`, hits the same `args_hash`, and skips the steps it already ran.

Three caveats:

- **Types round-trip through MessagePack.** A tuple argument comes back as a list, a `msgspec.Struct` as a dict — the same rule step results follow. A workflow that `isinstance`-checks a tuple parameter will behave differently on resume.
- **A secret argument replays as its placeholder.** Everything chowki persists is redacted first, so a run started with `api_key="sk-…"` is re-invoked with `api_key="[REDACTED:api_key:…]"`. `chowki_workflow_args_redacted` is logged when it happens. Pass secrets through configuration or the environment, not through workflow parameters.
- **An argument the codec cannot encode is not stored.** Arbitrary Python objects are not MessagePack-encodable; when one is passed, `chowki_workflow_args_not_persisted` is logged and the run record's `inputs` stays `None`. Such a run is resumable only if every parameter has a default — and a re-invocation of a run with no stored arguments and a required parameter raises `ChowkiStateError` naming the parameters, rather than a bare `TypeError`.

Arguments are recorded once, by the call that creates the run record; re-invoking the same `run_id` is a warm resume and does not overwrite them.

Values a reviewer must be able to change still belong in `current_run().state` — the state *is* restored from the last snapshot before the body re-executes, and it is what an `EDIT` decision patches. A workflow argument is fixed for the life of the run.

---

## The R4 Rule: Every Side Effect Lives in a Step

**Rule R4:** *Every side effect in a Chowki workflow must live inside a `@chowki.step`.*

Because warm resume re-executes the workflow function body from the top on resume, any side effect (sending emails, charging credit cards, writing files, calling external APIs) placed directly in the workflow body outside a `@chowki.step` will re-execute on every warm resume.

### Bad Example (R4 Violation)

```python
import chowki


def send_welcome_email(user_id: str) -> None:
    pass


@chowki.workflow
def bad_workflow(user_id: str = "u_123") -> None:
    # BAD: Side effect outside a step!
    # This print/email runs on EVERY warm resume!
    send_welcome_email(user_id)

    # Pauses here
    chowki.pause(reason="Await confirmation")
```

### Good Example (Compliant with R4)

```python
import chowki


def send_welcome_email(user_id: str) -> None:
    pass


@chowki.step
def deliver_welcome_email(user_id: str) -> bool:
    send_welcome_email(user_id)
    return True


@chowki.workflow
def good_workflow(user_id: str = "u_123") -> None:
    # GOOD: Side effect inside a step!
    # On warm resume, deliver_welcome_email is skipped and its cached result is returned.
    deliver_welcome_email(user_id)

    # Pauses here
    chowki.pause(reason="Await confirmation")
```

---

## Step Identity & Rename/Reorder Hazards

A step's identity (`step_id`) is computed using a per-step-name occurrence counter within the run:
- Format: `f"{step_name}#{n}"` where `step_name` is the function name (or custom `name`) and `n` is a zero-indexed occurrence counter for that step name within the run (e.g. `process_data#0`, `process_data#1`). Note that step identity uses a per-step-name counter rather than a global ordinal across all steps.
- The step identity is combined with a deterministic argument hash (`args_hash`) to form the idempotency key.

### Hazard: Renaming or Reordering Steps

If you modify workflow code while a run is paused or pending:
- **Renaming a step function:** The new step name produces a different step ID (`new_name#0` instead of `old_name#0`). `chowki` will treat it as an unexecuted step and re-run it.
- **Reordering occurrences of the same step:** Changing the relative order of repeated calls to the same step function changes occurrence indices `n`, invalidating matching cached step records downstream.

To safely deploy code changes to active runs, deploy non-breaking additions or wait for pending runs to complete.

---

## Crash Recovery (`recover_runs` & `rerun`)

If a process dies mid-execution (SIGKILL, host crash, unhandled error outside steps), the run remains in `RUNNING` status in storage.

### Step 1: Detect and Recover Stalled Runs

Call `recover_runs(engine)` (or run `chowki recover` in CLI) on process start. It resets every `RUNNING` run to `PENDING` and returns **all non-terminal runs** — `PENDING`, the ones it just reset, and `PAUSED`:

```python
import chowki
from chowki.config import get_engine
from chowki.types import RunStatus

engine = get_engine()
# Every non-terminal run: PENDING (including RUNNING ones just reset) and PAUSED
incomplete_runs = chowki.recover_runs(engine)
print(f"{len(incomplete_runs)} incomplete runs in storage")

# PAUSED runs wait for a human decision (resume with a token); the rest can be re-driven.
to_rerun = [run for run in incomplete_runs if run.status is not RunStatus.PAUSED]
```

`recover_runs` makes no liveness check: it cannot tell a crashed process's run from one a live worker is executing right now. Run it while no worker is executing runs against that database — on process start, before the fleet resumes work.

### Step 2: Rerun Recovered Workflows

Call `rerun()` (or run `chowki rerun <run_id>` in CLI) to re-execute the workflow from line 1. All previously completed steps are skipped automatically, and `ChowkiStateError` is raised only when no run with that ID exists in storage:

```python
import chowki

# Rerun the recovered run by ID
result = chowki.rerun("run-uuid-123")
```

---

## Step Failures, Retries & Operator Overrides

### Failed-Step Retry Matrix & Exponential Backoff

When a step fails with a transient or retryable exception (such as rate limits or tool execution errors), `chowki` handles automatic retries before requiring manual operator intervention:

- **Automatic Retries:** `chowki` automatically retries failed steps up to `max_auto_retries = 3` times (the default). This limit can be customized per step via `@chowki.step(retries=N)` or globally in `GuardrailConfig.max_auto_retries`.
- **Exponential Backoff:** Each retry sleeps for a full-jitter delay — a uniform random value between `0` and `min(retry_base_seconds * (2 ** attempt), retry_max_seconds)` (defaults: `retry_base_seconds = 1.0`, `retry_max_seconds = 30.0`).
- **Breaker Actions:** If `max_auto_retries` attempts are exhausted or a non-retryable exception occurs, the anomaly breaker decides what happens to the run:
  - `PAUSE` — the run auto-pauses (`PAUSED`), freezing state and minting a resume token so an operator can intervene, and `WorkflowPaused` is raised from the original error.
  - `ABORT` — the original exception propagates out of the workflow and the run is recorded `FAILED`. That is the fate of a non-retryable exception, a context-window error that one summarize attempt did not fix, a budget breach with `hard_budget_action` set to `"ABORT"`, and every failure while guardrails are switched off.

**An auto-pause does not need a gateway.** A configured `gateway` is only how reviewers are *notified*. With `gateway=None` (the default), a guardrail auto-pause still writes the `PauseRequest`, still moves the run to `PAUSED`, and still mints the resume token it carries on `WorkflowPaused.token` — nothing is downgraded to `ABORT`. The notification step is simply skipped, so the token reaches you through the raised `WorkflowPaused` instead, and an operator can always inspect the pause (`chowki runs show <run_id>`) or mint a fresh token (`chowki reissue-token <run_id>`) from the CLI.

### Manual Step Overrides (`release_step` & `complete_step`)

When a step fails permanently or is refused by guardrails/policy, an operator can manually override or release the step claim using administrative escape hatches:

| Operation | Python API | CLI Command | Purpose |
|---|---|---|---|
| **Release Claim** | `chowki.release_step(run_id, step_id)` | `chowki release-step <run_id> <step_id>` | Clears the step's idempotency claim without setting a result. Use when the side effect did NOT happen; the next workflow execution re-attempts the step function body. |
| **Force Complete** | `chowki.complete_step(run_id, step_id, result)` | `chowki complete-step <run_id> <step_id> -r '{"status": "ok"}'` | Records the step as `COMPLETED` with an operator-supplied result. On warm resume, `chowki` replays this result and skips the failed/refused step. |

Both operate on an attempt the run already recorded: if no step record exists for `step_id`, they raise `ChowkiStateError`. A force-completed result is only replayed when the step is reached with the same arguments — a different `args_hash` re-runs the body.

```python
import chowki

# Force-complete a failing payment step with a manual transaction ref
chowki.complete_step(
    run_id="run-uuid-123",
    step_id="charge_payment#0",
    result={"transaction_id": "manual-override-789", "status": "settled"},
)

# Resume the workflow -- charge_payment#0 now replays the operator result
chowki.rerun("run-uuid-123")
```

---

## What Can Go Wrong

1. **Violating Rule R4:** Placing network calls, file writes, or API calls outside `@chowki.step` causes duplicate execution on every resume.
2. **Code Deployment Drift During Active Pauses:** Modifying step function names or ordering while runs are paused in `PAUSED` status can cause cache misses during warm resume.
3. **Un-recovered Stalled Runs:** `rerun()` does not refuse a run still marked `RUNNING` — it re-executes it immediately, so calling it while the original process is in fact alive runs the same workflow twice. `rerun()` raises `ChowkiStateError` only when the run ID is unknown to storage. Recover on process start (`recover_runs(engine)` / `chowki recover`) rather than rerunning `RUNNING` runs against a live fleet.

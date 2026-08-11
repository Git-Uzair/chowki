# Warm Resume & Durable Execution

`chowki` achieves durable execution without custom DSLs, state machines, or bytecode manipulation. It works by re-executing the workflow function from the top down and skipping steps whose results were previously recorded in storage.

---

## Re-Execution and Memoisation

When a paused, interrupted, or recovered workflow is resumed via `chowki.resume()` or `chowki.rerun()`, `chowki` invokes the workflow function from line 1.

As execution encounters each `@chowki.step`:
1. `chowki` computes the step's identity (idempotency key based on step name, ordinal position, and arguments hash).
2. If storage contains a completed `StepRecord` with a matching idempotency key, `chowki` skips function execution and returns the stored result immediately.
3. If no completed record exists, the step function executes, its result is written to disk, and workflow execution proceeds to the next line.

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
def bad_workflow(user_id: str) -> None:
    # BAD: Side effect outside a step!
    # This print/email runs on EVERY warm resume!
    send_welcome_email(user_id)

    # Pauses here
    chowki.pause("Await confirmation")
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
def good_workflow(user_id: str) -> None:
    # GOOD: Side effect inside a step!
    # On warm resume, deliver_welcome_email is skipped and its cached result is returned.
    deliver_welcome_email(user_id)

    # Pauses here
    chowki.pause("Await confirmation")
```

---

## Step Identity & Rename/Reorder Hazards

A step's identity key is computed from:
- Workflow function name
- Step function name
- Step ordinal index (1st step, 2nd step, etc.)
- Deterministic hash of step arguments

### Hazard: Renaming or Reordering Steps

If you modify workflow code while a run is paused or pending:
- **Renaming a step function:** The new name will produce a different idempotency key. `chowki` will treat it as an unexecuted step and re-run it.
- **Reordering steps:** Changing the sequence of step calls changes step ordinals, invalidating matching cached step records downstream.

To safely deploy code changes to active runs, deploy non-breaking additions or wait for pending runs to complete.

---

## Crash Recovery (`recover_runs` & `rerun`)

If a process dies mid-execution (SIGKILL, host crash, unhandled error outside steps), the run remains in `RUNNING` status in storage.

### Step 1: Detect and Recover Stalled Runs

Call `recover_runs()` (or run `chowki recover` in CLI) to reset stalled `RUNNING` runs back to `PENDING`:

```python
import chowki

# Reclaims stalled runs across all tenant processes
recovered_count = chowki.recover_runs()
print(f"Recovered {recovered_count} stalled runs")
```

### Step 2: Rerun Recovered Workflows

Call `rerun()` (or run `chowki rerun <run_id>` in CLI) to re-trigger execution from line 1. All previously completed steps will be skipped automatically:

```python
import chowki

# Rerun the recovered run by ID
result = chowki.rerun("run-uuid-123")
```

---

## Step Override & Recovery Matrix (`release_step` & `complete_step`)

When a step fails or gets stuck in a retry loop, `chowki` allows explicit administrative interventions:

| Operation | Python API | CLI Command | Purpose |
|---|---|---|---|
| **Release Claim** | `chowki.release_step(run_id, step_id)` | `chowki release-step <run_id> <step_id>` | Clears the step's idempotency claim, forcing `chowki` to re-execute the step function on the next run attempt. |
| **Force Complete** | `chowki.complete_step(run_id, step_id, result)` | `chowki complete-step <run_id> <step_id> -r '{"status": "ok"}'` | Injects a manual result payload and marks the step `COMPLETED`, allowing the workflow to bypass a failing step on resume. |

```python
import chowki

# Force-complete a failing payment step with a manual transaction ref
chowki.complete_step(
    run_id="run-uuid-123",
    step_id="step-charge-456",
    result={"transaction_id": "manual-override-789", "status": "settled"},
)

# Resume the workflow
chowki.rerun("run-uuid-123")
```

---

## What Can Go Wrong

1. **Violating Rule R4:** Placing network calls, file writes, or API calls outside `@chowki.step` causes duplicate execution on every resume.
2. **Code Deployment Drift During Active Pauses:** Modifying step function names or ordering while runs are paused in `PAUSED` status can cause cache misses during warm resume.
3. **Un-recovered Stalled Runs:** Invoking `rerun()` on a run still marked `RUNNING` (because a dead process never updated its status) will raise `ChowkiStateError`. Always call `recover_runs()` first after process crashes.

# Human-in-the-Loop (HITL)

`chowki` provides built-in human-in-the-loop (HITL) pause gates, cryptographically signed single-use tokens, interactive approval CLI tools, and audit logging.

---

## Pause Gates (`chowki.pause`)

In a `@chowki.workflow`, invoke `chowki.pause()` whenever human intervention, approval, or state editing is required:

```python
import chowki


@chowki.step
def prep_release(env: str, cfg: dict[str, str]) -> None:
    # State written inside a step survives the pause and is what a reviewer patches.
    chowki.current_run().state["config"] = cfg


@chowki.step
def execute_deployment(env: str, cfg: dict[str, str]) -> str:
    return "deployed"


@chowki.workflow
def deployment_workflow(
    environment: str = "staging",
    config: dict[str, str] | None = None,
) -> str:
    cfg = config or {"memory_limit": "2Gi"}

    # Prepare release state in a step
    prep_release(environment, cfg)

    # Pause execution for human review
    chowki.pause(
        reason=f"Approve production deployment to {environment}",
        permitted_actions=("APPROVE", "REJECT", "EDIT"),
    )

    # Execution resumes here after APPROVE or EDIT
    return execute_deployment(environment, cfg)
```

Calling `chowki.pause()` suspends execution immediately and raises `WorkflowPaused`, transitioning the run status to `PAUSED`.

### Every Parameter of a Pausing Workflow Needs a Default

`chowki.resume()` (and `chowki.rerun()`) re-invoke the workflow function as `workflow_fn(run_id=run_id)` and pass **nothing else** — the original call arguments are not stored and not replayed. A pausing workflow whose parameters have no defaults therefore fails its own resume with `TypeError: missing 1 required positional argument`.

Give every parameter of a resumable workflow a default (as `deployment_workflow` does above), or take no parameters at all and read the run's inputs from inside steps or from `current_run().state`, which *is* restored on warm resume. Parameter values a reviewer must be able to change belong in the state (patchable with an `EDIT` decision), not in the signature.

---

## Pause Tokens & Lifecycle

When a run pauses, `chowki` issues an HMAC-SHA256 signed resume token:
- **Scope-Bound:** The token is bound specifically to the given `run_id` and pause gate `step_id`.
- **Single-Use Enforcement:** Each token contains a unique nonce. On resume, `chowki` consumes the nonce in atomic storage. Attempting to replay an already consumed token raises `ReplayedNonceError`.
- **TTL Expiration:** Tokens carry an expiration timestamp (default 24 hours). Expired tokens raise `ExpiredResumeToken`.
- **Reissuing Tokens:** If a token expires or is lost, reissue a fresh single-use token using `chowki.reissue_token(run_id)` or via CLI `chowki reissue-token <run_id>`.

---

## Human Decisions & State Patching (`EDIT`)

Resuming a paused workflow requires supplying a valid `Decision`:

| Decision | Behavior |
|---|---|
| `APPROVE` | Approves the gate; execution resumes past `chowki.pause()` down to the next boundary or completion. |
| `REJECT` | Declines the gate; run transitions to `REJECTED` and raises `HumanRejectedError`. |
| `EDIT` | Applies an RFC 6902 JSON patch to the state before resuming workflow execution. |
| `ESCALATE` | Escalates the review gate to higher-tier notification channels. |

### EDIT Decision with RFC 6902 JSON Patch

When a reviewer selects `EDIT`, `chowki` applies the JSON patch to the saved state before warm resume re-executes downstream steps:

```python
import chowki
from chowki.types import Decision

# Patch configuration argument before resuming deployment
patch = [{"op": "replace", "path": "/config/memory_limit", "value": "4Gi"}]

chowki.resume(
    run_id="run-uuid-123",
    token="valid-resume-token",
    decision=Decision.EDIT,
    patch=patch,
    note="Increased memory allocation prior to rollout",
)
```

---

## Audit Trail

Every resume decision is recorded in storage with an immutable audit entry:
- Decision outcome (`APPROVE`, `REJECT`, `EDIT`, `ESCALATE`)
- Applied RFC 6902 JSON patch (if any)
- Reviewer note and timestamp
- Token nonce and caller identification

---

## Console Gateway & CLI Walkthrough

### Console Gateway (`ConsoleGateway`)

`chowki` includes `ConsoleGateway` for terminal notifications, enabled by setting `ChowkiConfig(gateway=ConsoleGateway())`:

```python
import chowki
from chowki.hitl import ConsoleGateway

chowki.configure(gateway=ConsoleGateway())
```

When a workflow pauses, `ConsoleGateway` prints formatted notices to `stdout` with:
- **Pause Details:** Run ID, Workflow name, Step ID, Reason, Payload, Permitted Actions, and Reviewers.
- **Resume Token:** The generated HMAC-SHA256 single-use token.
- **Pre-formatted CLI Commands:** Auto-generated commands including `-m <module>` and `--db <path>` flags when non-default settings or script entry points are detected.

---

## Interactive CLI Walkthrough

`chowki` provides an interactive CLI for inspecting and resuming runs. When executing CLI commands against custom workflow modules or non-default database locations, use `-m <module>` and `--db <path>`:

### 1. List Workflow Runs

```bash
chowki --db ./.chowki/chowki.db runs list --status PAUSED
```

### 2. Inspect Run State & Pause Gate

```bash
chowki --db ./.chowki/chowki.db runs show <run_id>
```

### 3. Approve or Reject via CLI

```bash
# Approve a paused run
chowki --db ./.chowki/chowki.db -m my_module resume <run_id> --token <token> --decision APPROVE --note "Approved by SRE"

# Reject a paused run
chowki --db ./.chowki/chowki.db -m my_module resume <run_id> --token <token> --decision REJECT --note "Rejected: missing approval ticket"
```

### 4. Edit State via CLI

```bash
chowki --db ./.chowki/chowki.db -m my_module resume <run_id> --token <token> --decision EDIT --patch '[{"op": "replace", "path": "/timeout", "value": 30}]'
```

### 5. Reissue Expired Token

```bash
chowki --db ./.chowki/chowki.db -m my_module reissue-token <run_id>
```

---

## What Can Go Wrong

1. **Token Replay Attempts:** Reusing a resume token raises `ReplayedNonceError`. Always obtain a fresh token via `reissue_token` if needed.
2. **Unsupported Decision Actions:** Requesting `Decision.EDIT` when `permitted_actions` did not include `"EDIT"` raises `InvalidResumeToken`.
3. **Invalid JSON Patch Paths:** Passing a patch path that does not exist in the current state dictionary raises an error during RFC 6902 patch execution.

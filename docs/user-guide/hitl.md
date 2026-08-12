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

### A Pausing Workflow Resumes With the Arguments It Started With

`chowki.resume()`, `chowki.aresume()` and `chowki.rerun()` re-invoke the workflow as `workflow_fn(*args, run_id=run_id, **kwargs)`, using the arguments of the original call as they were persisted (redacted) on the run record. A required parameter is fine, and the entity the reviewer approved is the entity the resumed run acts on — a defaulted parameter can no longer quietly swap it.

Two caveats carry over from the codec: a secret passed as an argument replays as its `[REDACTED:…]` placeholder, so pass secrets through configuration instead; and an argument the codec cannot encode is not stored at all (logged as `chowki_workflow_args_not_persisted`), leaving that run resumable only if every parameter has a default.

Values a reviewer must be able to change still belong in `current_run().state`, which is what an `EDIT` decision patches. A workflow argument is fixed for the life of the run — see [Warm Resume](warm-resume.md#workflow-arguments-are-replayed-from-the-run-record).

### A Step That Pauses Must Be `idempotent=False`

Pausing from the workflow body, as above, always works. Pausing from **inside a `@chowki.step`** is also supported — the step interceptor re-raises `WorkflowPaused` rather than marking the step failed — but only if that step opts out of idempotency:

```python
@chowki.step(idempotent=False)  # required: the gate must be re-enterable
def issue_refund(order_id: str, amount: float) -> str:
    if amount > 100:
        chowki.pause(reason="Large refund", payload={"order_id": order_id})
    return _post_refund(order_id, amount)


@chowki.step  # the side effect keeps its exactly-once claim
def _post_refund(order_id: str, amount: float) -> str: ...
```

A default step claims its idempotency key *before* its body runs. `chowki.pause()` raises straight past that claim without releasing it, so when the resume replays the body and re-enters the step, the claim is still outstanding and `chowki` refuses to continue:

```text
ChowkiStorageError: step issue_refund#0 of run r1 has an unfinished idempotent attempt:
idempotency key b9c6a883... is already claimed.
```

That error is the guard working as designed — it cannot tell a pause from a process that died holding the claim, so it declines to guess. Declaring the pausing step `idempotent=False` removes the claim and makes the gate re-enterable; keeping the irreversible work in a **nested default step** means the transfer still cannot happen twice. `REJECT` raises `HumanRejectedError` out of the workflow, and the nested step is never reached.

This matters most when an agent framework owns the loop. The framework decides *when* to call a tool, so the gate has to live inside the tool rather than in the workflow body — see [`examples/python/integrations/`](../../examples/python/integrations/).

If you hit the error on a run that is already stuck, the escape hatches named in the message apply: `chowki.release_step(run_id, step_id)` if the side effect did **not** happen, or `chowki.complete_step(...)` to record it if it did.

### Addressing the Notice: `channel` and `reviewers`

`chowki.pause()` takes two more arguments than the examples above use:

```python
chowki.pause(
    reason="Refund over $1,000 needs a human",
    payload={"order_id": "ord-4417", "amount": 1_240.00},
    permitted_actions=("APPROVE", "REJECT", "EDIT"),
    reviewers=("U12345", "payments-oncall@example.com"),  # who should be asked
    channel="slack",  # where to ask them
)
```

Both are recorded on the `PauseRequest` and delivered to your gateway on the `PauseNotice` as `notice.reviewers` and `notice.channel`. Both survive a restart: a resumed run reads them back off the stored request rather than from the process that paused.

> **What chowki does not do with `channel` — read this before relying on it.**
>
> `chowki` **carries** these values; it does not route on them. There is no dispatch table mapping `"slack"` to a Slack adapter. `ConsoleGateway`, the only gateway that ships, ignores `channel` entirely and prints to stdout no matter what you pass. Slack, Teams, and REST adapters are Phase 4 and unbuilt — see the channel row in [`docs/features.md`](../features.md).
>
> So `channel="slack"` today records an intention and hands it to whatever gateway you configured. It does not deliver anything to Slack on its own.

That makes the pair genuinely useful for exactly one thing: **one gateway of your own, fanning out to several destinations.** Rather than configuring a different engine per destination, read the fields off the notice and dispatch yourself:

```python
class RoutingGateway:
    name = "routing"

    def notify(self, notice: PauseNotice) -> GatewayHandle:
        if notice.channel == "slack":
            slack_client.post(channel="#approvals", text=_render(notice), users=notice.reviewers)
        elif notice.channel == "email":
            send_mail(to=list(notice.reviewers), subject=notice.reason, body=_render(notice))
        else:
            print(_render(notice))
        return GatewayHandle(channel=notice.channel, message_id=notice.run_id)
```

`reviewers` is carried, not enforced. Nothing checks that the person who presents the resume token is one of them — the token is scope-bound to the run and gate, not to an identity. Role enforcement and N-of-M approval are Phase 4. If you need to restrict *who* may approve, do it in the surface that holds the token (your web handler, your Slack action handler), not by listing reviewers here.

To widen the audience mid-gate, use `ESCALATE`: it mints a fresh token and leaves the run paused, and it replaces `reviewers` on the stored pause from the `actor` mapping — there is no separate `reviewers=` argument on `resume()`:

```python
chowki.resume(
    run_id="run-1",
    token=token,
    decision=chowki.Decision.ESCALATE,
    actor={"reviewers": ["U99999", "director@example.com"]},
)
```

Anything other than a list under `actor["reviewers"]` leaves the existing reviewers in place. The gateway is notified again with the new token, so the next notice carries the widened list.

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
4. **Pausing Inside a Default Step:** A `@chowki.step` that reaches `chowki.pause()` without `idempotent=False` pauses fine but fails its own resume with `ChowkiStorageError: ... unfinished idempotent attempt`. See [A Step That Pauses Must Be `idempotent=False`](#a-step-that-pauses-must-be-idempotentfalse).

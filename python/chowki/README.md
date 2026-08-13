<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <samp>
&nbsp;&nbsp;&nbsp;██████╗██╗&nbsp;&nbsp;██╗&nbsp;██████╗&nbsp;██╗&nbsp;&nbsp;&nbsp;&nbsp;██╗██╗&nbsp;&nbsp;██╗██╗<br/>
&nbsp;&nbsp;██╔════╝██║&nbsp;&nbsp;██║██╔═══██╗██║&nbsp;&nbsp;&nbsp;&nbsp;██║██║&nbsp;██╔╝██║<br/>
&nbsp;&nbsp;██║&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;███████║██║&nbsp;&nbsp;&nbsp;██║██║&nbsp;█╗&nbsp;██║█████╔╝&nbsp;██║<br/>
&nbsp;&nbsp;██║&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;██╔══██║██║&nbsp;&nbsp;&nbsp;██║██║███╗██║██╔═██╗&nbsp;██║<br/>
&nbsp;&nbsp;╚██████╗██║&nbsp;&nbsp;██║╚██████╔╝╚███╔███╔╝██║&nbsp;&nbsp;██╗██║<br/>
&nbsp;&nbsp;&nbsp;╚═════╝╚═╝&nbsp;&nbsp;╚═╝&nbsp;╚═════╝&nbsp;&nbsp;╚══╝╚══╝&nbsp;╚═╝&nbsp;&nbsp;╚═╝╚═╝
  </samp>
</p>

<p align="center">
  <b>Your agent crashed on step 9 of 10. chowki makes step 10 the only thing that runs again.</b>
</p>

<p align="center">
  <a href="https://pypi.org/project/chowki/"><img src="https://img.shields.io/pypi/v/chowki.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/chowki/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg" alt="Python"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://github.com/Git-Uzair/chowki/actions/workflows/ci.yml"><img src="https://github.com/Git-Uzair/chowki/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

> **State savers restore *state*. chowki memoizes *step results* — so on resume, the LLM calls and API requests that already succeeded do not happen again.**

Coming from LangGraph? Its `SqliteSaver` restores the graph state at a checkpoint, and
its own docs say the nodes after it re-execute — "including any LLM calls, API requests,
and interrupts". chowki records the *result* of every `@chowki.step`, so those calls are
served from the record instead of re-issued.

- **No determinism tax** — no replay-safe workflow body, no journal-mismatch errors. Your
  code may call `random`, `time.time()`, or an API directly; non-determinism is isolated
  at the step boundary.
- **Approval gates with cryptographic provenance** — one single-use HMAC-SHA256 token per
  run and gate, four decisions (`APPROVE` / `REJECT` / `EDIT` as an RFC 6902 patch /
  `ESCALATE`), and an append-only audit log no adapter is permitted to delete from.
- **Secret redaction that cannot be switched off** — key names, a combined
  provider-credential regex, and Shannon entropy over step arguments, state, and logs
  *before* anything is persisted.
- **Agent cost budgets** — token and spend ceilings with an 80% soft warning re-seeded
  from persisted usage on resume, three tiers of loop detection, and `max_steps_per_run`.

`chowki` (Urdu *چوکی* — a checkpost, a place where things are stopped and inspected) is an
in-process control plane for LLM agents. It adds durable state, approval gates, secret
redaction, and runaway-loop guardrails to agent code you have already written — as
decorators, with no server, no worker pool, and no sidecar.

It is not an agent framework. It goes **underneath** the one you already use.

---

## Why

An LLM agent is an expensive, non-deterministic, side-effecting loop. Four things go wrong,
and every one of them costs money or trust:

| Problem | Without chowki | With chowki |
| --- | --- | --- |
| Process dies at step 9 of 10 | Re-run from scratch, re-pay for all 9 LLM calls | Completed steps are memoised; only step 10 runs |
| Agent is about to email a customer | You find out afterwards | `chowki.pause()` suspends the run and mints a signed approval token |
| An API key lands in the agent's state | It is now in your logs and your database | Redacted to `[REDACTED:api_key:9f2c…]` before it is persisted |
| Agent loops on the same tool call forever | You notice on the bill | Loop detection raises after N repeats; token budgets pause the run |

---

## Install

```bash
pip install chowki
```

Optional OpenTelemetry span export:

```bash
pip install "chowki[otel]"
```

Requires Python 3.11+. Runtime dependencies are `msgspec`, `jsonpatch`, `jsonpointer`,
`cryptography`, `structlog`, and `opentelemetry-api`.

---

## 60-second quickstart

```python
import chowki


@chowki.step
def fetch_invoice(invoice_id: str) -> dict[str, object]:
    return {"id": invoice_id, "amount": 4_200}


@chowki.step(idempotent=False)  # can pause -> must be re-enterable
def pay_invoice(invoice: dict[str, object]) -> str:
    chowki.pause(
        reason="Payment over $1,000 needs a human",
        payload=invoice,
        permitted_actions=("APPROVE", "REJECT", "EDIT"),
    )
    return _transfer(invoice)  # only runs after approval


@chowki.step  # the real side effect: exactly-once
def _transfer(invoice: dict[str, object]) -> str:
    return f"paid {invoice['amount']} for {invoice['id']}"


@chowki.workflow
def billing_agent(invoice_id: str = "inv-1") -> str:
    return pay_invoice(fetch_invoice(invoice_id))
```

Run it, and it stops at the gate:

```python
try:
    billing_agent(invoice_id="inv-1", run_id="run-1")
except chowki.WorkflowPaused as paused:
    token = paused.token  # single-use, HMAC-signed, bound to this run + gate
```

Approve it — from this process, another process, a web handler, or the CLI:

```python
chowki.resume(run_id="run-1", token=token, decision=chowki.Decision.APPROVE)
```

`fetch_invoice` is **not** called again. Neither is anything else that already completed.

```bash
chowki runs list                       # what is paused right now
chowki runs show run-1                 # full state, history, audit log
chowki resume run-1 --token '<token>' --decision APPROVE
```

### The one rule

Every side effect belongs inside a `@chowki.step`. `resume()` re-executes the workflow
body from the top, and only steps are memoised — anything outside one runs again.

### The one gotcha

A step whose body can reach `chowki.pause()` must be declared `idempotent=False`. The
idempotency claim is taken *before* the body runs and a pause does not release it, so a
default step that pauses cannot be re-entered on resume. Keep the actual side effect in a
nested default step, as `_transfer` above — it keeps exactly-once protection while the gate
stays re-enterable.

---

## Works with the framework you already use

chowki wraps *tools*, so it composes with any framework that calls plain Python functions.
The framework decorator goes on the outside; `@chowki.step` goes underneath it, so the
framework builds its schema from your signature and chowki intercepts the call:

```python
@tool  # LangChain / CrewAI / OpenAI Agents SDK / PydanticAI
@chowki.step  # chowki sees every invocation
def issue_refund(order_id: str, amount: float) -> str: ...
```

Runnable examples for each, in
[`examples/python/integrations/`](https://github.com/Git-Uzair/chowki/tree/main/examples/python/integrations):

| Framework | Example | What chowki adds |
| --- | --- | --- |
| **LangChain / LangGraph** | [`langgraph_refund_agent.py`](https://github.com/Git-Uzair/chowki/blob/main/examples/python/integrations/langgraph_refund_agent.py) | Tools memoised across a crash; approval gate before the refund |
| **CrewAI** | [`crewai_refund_crew.py`](https://github.com/Git-Uzair/chowki/blob/main/examples/python/integrations/crewai_refund_crew.py) | Per-agent budgets; the crew resumes instead of restarting |
| **OpenAI Agents SDK** | [`openai_agents_refund.py`](https://github.com/Git-Uzair/chowki/blob/main/examples/python/integrations/openai_agents_refund.py) | `@function_tool` + durable steps; usage reported per turn |
| **Pydantic AI** | [`pydantic_ai_refund.py`](https://github.com/Git-Uzair/chowki/blob/main/examples/python/integrations/pydantic_ai_refund.py) | Typed tools stay typed; gate rides on `RunContext` deps |
| **No framework** | [`anthropic_tool_loop.py`](https://github.com/Git-Uzair/chowki/blob/main/examples/python/integrations/anthropic_tool_loop.py) | A hand-written Claude tool loop, made durable |
| **Slack approvals** *(a channel, not a framework)* | [`slack_approvals.py`](https://github.com/Git-Uzair/chowki/blob/main/examples/python/integrations/slack_approvals.py) | Gate arrives in Slack; an HMAC-verified click resumes the run over HTTP |

> **Slack is a worked example, not a shipped adapter.** A first-party Slack gateway is roadmap Phase 4. `slack_approvals.py` is what you write today against extension points that exist now — `ConsoleGateway` implements the same protocol, and `PauseNotice` caps the token below Slack's button limit on purpose.

> **Scope, honestly:** chowki memoises what you decorate. Model calls made *inside* a
> framework's own loop are re-issued on resume unless you wrap them too — each example
> shows where that hook goes for that framework.

---

## What you get

- **Warm resume & crash recovery** — top-down re-execution with step memoisation. `chowki
  recover` revives runs whose process was killed; `chowki rerun` finishes them without
  repeating completed work.
- **Human-in-the-loop gates** — `chowki.pause()` mints a single-use HMAC-SHA256 token bound
  to one run and one gate. Decisions are `APPROVE`, `REJECT`, `EDIT` (an RFC 6902 patch the
  reviewer applies to state), and `ESCALATE`. Every decision is written to an audit log.
- **Always-on secret redaction** — regex patterns plus Shannon-entropy scanning, applied to
  step arguments, state snapshots, and logs before anything is persisted. Placeholders are
  stable HMAC digests, so diffs stay readable without exposing the secret.
- **Guardrails** — token/cost budgets with soft-warning thresholds, and three tiers of loop
  detection: repeated tool-call hashes, Levenshtein-similar text (`chowki.record_text`), and
  cycles in the delegation graph (`chowki.record_transition`).
- **Encryption at rest** — optional AES-256-GCM over snapshot payloads, keyed from
  `CHOWKI_MASTER_KEY`, with the tenant and run bound in as AAD.
- **Inspection** — `chowki.inspect_run()` and `chowki runs show` read full state, step
  history, and the audit trail without disturbing a live run.
- **Storage you already have** — SQLite by default (a file, created on first use), in-memory
  for tests, or your own adapter.

---

## Configuration

```python
import chowki
from chowki.guardrails import GuardrailConfig
from chowki.hitl.console import ConsoleGateway

chowki.configure(
    db_path="./agent.db",
    resume_secret=b"...",  # 32+ bytes; without it tokens die on restart
    gateway=ConsoleGateway(),  # or your own Slack / web gateway
    encrypt_at_rest=True,  # reads CHOWKI_MASTER_KEY
    guardrails=GuardrailConfig(max_token_budget=100_000, soft_budget_threshold=0.8),
)
```

Set `resume_secret` in production. Without it chowki signs with an ephemeral per-process
key and warns — tokens minted before a restart will not verify after it.

---

## Limits worth knowing before you adopt

chowki is single-process, single-writer per run. Two processes writing the same run to one
SQLite file is not supported, and it says so rather than corrupting your state. Distributed
workers, Postgres/Redis adapters, and multi-region leasing are roadmap, not shipped.

Full detail: [Limits & Operational Boundaries](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/limits.md).

---

## Documentation

| | |
| --- | --- |
| [User Guide](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/index.md) | Start here |
| [Core Concepts](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/concepts.md) | Runs, steps, state, base/delta snapshots |
| [Warm Resume](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/warm-resume.md) | Re-execution, memoisation, the R4 rule, crash recovery |
| [Guardrails](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/guardrails.md) | Budgets, loop detection, provider usage recipes |
| [HITL](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/hitl.md) | Gates, tokens, decisions, CLI walkthrough |
| [Configuration & Security](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/configuration.md) | `ChowkiConfig`, secrets, encryption, multi-tenancy |
| [Production Resumes](https://github.com/Git-Uzair/chowki/blob/main/docs/user-guide/resuming-in-production.md) | FastAPI / Flask, async, HTTP status mapping |

---

## Status

Alpha, and honest about it: the API may shift before 1.0. The Python SDK is the reference
implementation; a Node/TypeScript SDK is next, built against a shared conformance spec.

MIT licensed. Issues and PRs: <https://github.com/Git-Uzair/chowki>.

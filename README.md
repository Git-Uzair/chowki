<!-- markdownlint-disable MD033 MD041 -->
<div align="center">
  <samp>
&nbsp;&nbsp;&nbsp;██████╗██╗&nbsp;&nbsp;██╗&nbsp;██████╗&nbsp;██╗&nbsp;&nbsp;&nbsp;&nbsp;██╗██╗&nbsp;&nbsp;██╗██╗<br/>
&nbsp;&nbsp;██╔════╝██║&nbsp;&nbsp;██║██╔═══██╗██║&nbsp;&nbsp;&nbsp;&nbsp;██║██║&nbsp;██╔╝██║<br/>
&nbsp;&nbsp;██║&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;███████║██║&nbsp;&nbsp;&nbsp;██║██║&nbsp;█╗&nbsp;██║█████╔╝&nbsp;██║<br/>
&nbsp;&nbsp;██║&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;██╔══██║██║&nbsp;&nbsp;&nbsp;██║██║███╗██║██╔═██╗&nbsp;██║<br/>
&nbsp;&nbsp;╚██████╗██║&nbsp;&nbsp;██║╚██████╔╝╚███╔███╔╝██║&nbsp;&nbsp;██╗██║<br/>
&nbsp;&nbsp;&nbsp;╚═════╝╚═╝&nbsp;&nbsp;╚═╝&nbsp;╚═════╝&nbsp;&nbsp;╚══╝╚══╝&nbsp;╚═╝&nbsp;&nbsp;╚═╝╚═╝
  </samp>

  <h1 align="center">chowki</h1>

  <p><b>Your agent crashed on step 9 of 10. chowki makes step 10 the only thing that runs again.</b></p>

  <p>
    <a href="https://pypi.org/project/chowki/"><img src="https://img.shields.io/pypi/v/chowki.svg" alt="PyPI"></a>
    <a href="https://pypi.org/project/chowki/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg" alt="Python"></a>
    <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
    <a href="https://github.com/Git-Uzair/chowki/actions/workflows/ci.yml"><img src="https://github.com/Git-Uzair/chowki/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  </p>
</div>

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
in-process control plane for LLM agents: durable state, human approval gates, secret
redaction, and runaway-loop guardrails, added to agent code you have already written.

Decorators, not infrastructure. No server, no worker pool, no sidecar — state is committed
to a local SQLite file before a step returns.

**It is not an agent framework.** It goes underneath the one you already use.

```bash
pip install chowki
```

```python
import chowki


@chowki.step  # memoised, snapshotted, redacted
def fetch_invoice(invoice_id: str) -> dict[str, object]:
    return {"id": invoice_id, "amount": 4_200}


@chowki.step(idempotent=False)  # can pause -> must be re-enterable
def pay_invoice(invoice: dict[str, object]) -> str:
    chowki.pause(reason="Payment over $1,000 needs a human", payload=invoice)
    return _transfer(invoice)


@chowki.step  # the real side effect: exactly-once
def _transfer(invoice: dict[str, object]) -> str:
    return f"paid {invoice['amount']} for {invoice['id']}"


@chowki.workflow
def billing_agent(invoice_id: str = "inv-1") -> str:
    return pay_invoice(fetch_invoice(invoice_id))
```

The run stops at the gate with a single-use signed token. Approve it from anywhere — this
process, a web handler, or `chowki resume run-1 --token <token> --decision APPROVE` — and
`fetch_invoice` is not called again.

Full package documentation, including install options and configuration:
**[`python/chowki/README.md`](python/chowki/README.md)**.

---

## Works with the framework you already use

chowki wraps *tools*, so it composes with anything that calls plain Python functions. The
framework's decorator goes on the outside, `@chowki.step` underneath it — the framework
builds its schema from your signature, chowki intercepts the call.

Runnable examples: **[`examples/python/integrations/`](examples/python/integrations/)**

| Framework | Example |
| --- | --- |
| LangChain / LangGraph | [`langgraph_refund_agent.py`](examples/python/integrations/langgraph_refund_agent.py) |
| CrewAI | [`crewai_refund_crew.py`](examples/python/integrations/crewai_refund_crew.py) |
| OpenAI Agents SDK | [`openai_agents_refund.py`](examples/python/integrations/openai_agents_refund.py) |
| Pydantic AI | [`pydantic_ai_refund.py`](examples/python/integrations/pydantic_ai_refund.py) |
| No framework (Claude tool loop) | [`anthropic_tool_loop.py`](examples/python/integrations/anthropic_tool_loop.py) |
| Slack approvals — a *channel*, not a framework | [`slack_approvals.py`](examples/python/integrations/slack_approvals.py) |

The Slack one integrates a *channel* rather than a framework: a gate arrives as Approve/Reject buttons carrying the resume token, and the click comes back to an endpoint that verifies Slack's signature and resumes the run. **A first-party Slack adapter is roadmap Phase 4 and not shipped** — that example is what you write today, on extension points (`ChannelGateway.notify` / `verify_ingress` / `parse_action`) that exist now.

Standalone showcases, no API key required:

| Example | Shows |
| --- | --- |
| [`quickstart.py`](examples/python/quickstart.py) | Pause, console notice, warm resume with an `EDIT` patch |
| [`agent_review.py`](examples/python/agent_review.py) | Budget warnings, approval gate, and a `kill -9` recovered with **zero** repeated LLM calls |
| [`fastapi_approvals.py`](examples/python/fastapi_approvals.py) | Approval endpoints in a web app |

---

## Documentation

**Planning work in this repo? Start at [`docs/features.md`](docs/features.md)** — the
language-agnostic feature catalog and Python/Node parity matrix, with the plan-generation
workflow in its header. Phase scope lives in [`docs/plans/00-roadmap.md`](docs/plans/00-roadmap.md).

| Guide | |
| --- | --- |
| [User Guide](docs/user-guide/index.md) | Entry point |
| [Core Concepts](docs/user-guide/concepts.md) | Runs, steps, state, base/delta snapshots, engine model |
| [Warm Resume](docs/user-guide/warm-resume.md) | Re-execution, memoisation, **the R4 rule**, crash recovery |
| [Guardrails](docs/user-guide/guardrails.md) | Budgets, the three loop-detection tiers, provider recipes |
| [HITL](docs/user-guide/hitl.md) | Gates, HMAC tokens, decisions, CLI walkthrough |
| [Configuration & Security](docs/user-guide/configuration.md) | `ChowkiConfig`, `resume_secret`, encryption, multi-tenancy |
| [Limits](docs/user-guide/limits.md) | Single-writer boundary, SQLite concurrency, what is *not* guaranteed |
| [Production Resumes](docs/user-guide/resuming-in-production.md) | FastAPI / Flask, async background resumes, HTTP status mapping |

Design research behind the implementation — serialization, durable execution, guardrails,
HITL, cross-SDK parity — is in [`docs/research/`](docs/research/). Those documents are
normative: [`07-cross-sdk-parity.md`](docs/research/07-cross-sdk-parity.md) pins the
byte-level algorithms every SDK must reproduce.

---

## Repository layout

This is a polyglot monorepo. The Python SDK is the reference implementation; the Node SDK
is built against the same conformance spec.

```
python/chowki/         Python SDK  — src/, tests/ (unit, integration, benchmarks)
node/                  Node SDK    — planned, phase 3
spec/v1/               Cross-SDK conformance spec + test vectors
examples/python/       Runnable examples, incl. integrations/ per framework
docs/                  features.md (catalog), user-guide/, research/, plans/
scripts/               Layout guard, wheel smoke test
```

Structure is enforced — `scripts/check_layout.py` fails CI if a package lands in the wrong
place.

---

## Development

The repo is a [uv](https://docs.astral.sh/uv/) workspace.

```bash
uv sync --all-extras --dev
```

```bash
uv run pytest python/chowki/tests/unit python/chowki/tests/integration -q   # 523 tests
uv run pytest python/chowki/tests/benchmarks --benchmark-only -q            # perf budgets
uv run ruff format --check . && uv run ruff check . && uv run pyright       # static gate
uv run python scripts/check_layout.py                                       # layout guard
```

CI runs the suite on Python 3.11/3.12/3.13 across Ubuntu, macOS, and Windows, plus a
lowest-declared-dependency job, the performance budgets, and a wheel smoke test.

**Performance budgets are code, not prose.**
[`python/chowki/tests/benchmarks/budgets.py`](python/chowki/tests/benchmarks/budgets.py) is
the normative registry and CI fails on a breach. Changing a number there is an
architectural decision that requires a matching update in `docs/research/`.

Contributor conventions live in [`AGENTS.md`](AGENTS.md).

---

## Status

**Alpha.** The API may shift before 1.0. chowki is single-process and single-writer per run
by design — see [Limits](docs/user-guide/limits.md) before adopting it for distributed
workloads; Postgres/Redis adapters and multi-region leasing are roadmap, not shipped.

MIT licensed — see [LICENSE](LICENSE).

# chowki + your agent framework

chowki is not an agent framework. It goes underneath the one you already use, at the tool
boundary, and it is the same three lines everywhere:

```python
@tool  # your framework's decorator, outermost
@chowki.step  # chowki, underneath
def issue_refund(order_id: str, amount: float) -> str: ...
```

`@chowki.step` uses `functools.wraps`, so the framework still sees the real name,
docstring, and type hints and generates exactly the schema it would without chowki.

Every example below implements the same agent — *look up an order, then refund it* — so you
can diff two files and see only what that framework changes. The shared tools live in
[`_refund_domain.py`](_refund_domain.py); the per-framework files are the wiring.

| File | Framework | Install |
| --- | --- | --- |
| [`langgraph_refund_agent.py`](langgraph_refund_agent.py) | LangChain / LangGraph | `pip install "langchain>=1.0" langchain-anthropic` |
| [`crewai_refund_crew.py`](crewai_refund_crew.py) | CrewAI | `pip install crewai` |
| [`openai_agents_refund.py`](openai_agents_refund.py) | OpenAI Agents SDK | `pip install openai-agents` |
| [`pydantic_ai_refund.py`](pydantic_ai_refund.py) | Pydantic AI | `pip install pydantic-ai` |
| [`anthropic_tool_loop.py`](anthropic_tool_loop.py) | none — a hand-written Claude loop | `pip install anthropic` |
| [`slack_approvals.py`](slack_approvals.py) | Slack approvals + FastAPI ingress | `pip install fastapi uvicorn slack-sdk` |

These need an API key and the framework installed, so they are **not** run in CI. The
zero-dependency showcases in the parent directory — [`quickstart.py`](../quickstart.py) and
[`agent_review.py`](../agent_review.py) — are.

---

## Approving in Slack, resuming over HTTP

[`slack_approvals.py`](slack_approvals.py) is the odd one out: it integrates a *channel*
rather than an agent framework. A workflow pauses, the gate arrives in Slack as Approve /
Reject buttons carrying the resume token, and the click comes back to a FastAPI endpoint
that verifies Slack's HMAC signature and calls `chowki.resume()`.

**A first-party Slack adapter is roadmap Phase 4 and is not shipped.** That example is what
you write today to get the same result — and the extension points it uses are not
placeholders: `ChannelGateway.notify`, `verify_ingress`, and `parse_action` all exist now,
`ConsoleGateway` is a reference implementation of the same protocol, and `PauseNotice`
caps the token below Slack's 2000-character button limit on purpose. When the built-in
adapter lands you delete the gateway class; your workflow code does not change.

Two things chowki deliberately leaves to you there: `reviewers` is carried but never
enforced (the token authorises a run and gate, not a person), and `resume()` re-executes
the workflow body in the calling process, so anything slow after the gate belongs in a
background task.

---

## The rule that makes gates work

An approval gate inside a tool is the whole point of the integration, and there is exactly
one thing to get right:

> **A `@chowki.step` whose body can reach `chowki.pause()` must be declared
> `idempotent=False`.**

A default step claims an idempotency key *before* its body runs. `pause()` raises
`WorkflowPaused` straight past that claim without releasing it, so when the resume replays
the loop and re-enters the step, chowki refuses:

```text
ChowkiStorageError: step issue_refund#0 of run r1 has an unfinished idempotent attempt
```

Declaring the gate step `idempotent=False` makes it re-enterable. Keep the irreversible
work in a nested default step, and you get both halves:

```python
@chowki.step(idempotent=False)  # gate: re-enterable
def issue_refund(order_id: str, amount: float) -> str:
    if amount > 100:
        chowki.pause(reason=..., payload=..., permitted_actions=("APPROVE", "REJECT"))
    return _post_refund(order_id, amount)


@chowki.step  # transfer: idempotency-claimed, happens once
def _post_refund(order_id: str, amount: float) -> str: ...
```

`REJECT` raises `HumanRejectedError` out of the workflow and `_post_refund` is never
reached.

---

## What chowki does and does not cover

chowki memoises **what you decorate**.

Your tools are decorated, so they are durable: on resume, a completed tool returns its
recorded result instead of running again, and an irreversible one cannot fire twice.

A framework's **own model calls are not** — they happen inside `create_agent`, `kickoff()`,
or `Runner.run()`, where chowki never sees them. A resumed run re-issues them, and you pay
again. Each framework file has a `durable_model` / `durable_llm` / `call_model_once` helper
showing where that framework lets you route the model call through a step; wire it up if
re-billing matters for your workload.

The no-framework example, [`anthropic_tool_loop.py`](anthropic_tool_loop.py), has no such
caveat: you own the loop, so the model call is a `@chowki.step` like everything else. It is
the clearest picture of what full coverage looks like.

---

## Running one

```bash
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY for the Agents SDK example
uv run python examples/python/integrations/langgraph_refund_agent.py
```

The agent looks up `ord-4417` ($240), calls `issue_refund`, and stops — the console gateway
prints the pause notice, the permitted actions, and a copy-pasteable resume command. The
example then approves it in-process and prints the refund ledger, which contains exactly one
entry, with the order lookup never repeated.

To drive the gate yourself instead, take the token from the notice:

```bash
chowki --db ./langgraph_refund.db runs list
chowki --db ./langgraph_refund.db runs show langgraph-refund-1
chowki --db ./langgraph_refund.db -m examples.python.integrations.langgraph_refund_agent \
    resume langgraph-refund-1 --token '<token>' --decision APPROVE
```

`--decision REJECT` refuses the refund; `--decision EDIT` takes an RFC 6902 patch and
rewrites run state before the workflow body replays.

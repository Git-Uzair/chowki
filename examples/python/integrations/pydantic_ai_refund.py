"""chowki + Pydantic AI.

    pip install chowki pydantic-ai

Pydantic AI's pitch is that everything crossing the model boundary is validated: tool
arguments are parsed into Python types, bad arguments come back to the model as a retry,
and outputs are typed. It gives you correctness *within* a run.

chowki gives you the axis Pydantic AI leaves alone — what happens *between* runs, and what
happens when a run should stop and wait for a person.

`@agent.tool_plain` registers a function whose signature Pydantic AI turns into a schema.
`@chowki.step` sits underneath, and `functools.wraps` keeps the annotations intact, so
validation behaves exactly as it would without chowki::

    @agent.tool_plain     # outermost: Pydantic AI validates and schematises
    @chowki.step(...)     # innermost: chowki records, memoises, gates
    def issue_refund(order_id: str, amount: float) -> str: ...

Use `@agent.tool` instead when the tool needs `RunContext` — put chowki underneath there
too, and it records the non-context arguments.

Caveat: chowki memoises what you decorate. Pydantic AI's model requests happen inside its
own loop, so a resume re-issues them unless you wrap the model — see `durable_model`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chowki
from chowki.hitl.console import ConsoleGateway

sys.path.insert(0, str(Path(__file__).parent))

from _refund_domain import executed_refunds, issue_refund, lookup_order, report_usage

try:
    from pydantic_ai import Agent, RunContext
except ImportError as exc:  # pragma: no cover - example dependency
    raise SystemExit("This example needs Pydantic AI:  pip install pydantic-ai") from exc


@dataclass
class SupportDeps:
    """Typed dependencies carried on `RunContext` for the whole run."""

    reviewer_email: str


agent = Agent(
    "anthropic:claude-opus-5",
    deps_type=SupportDeps,
    system_prompt=(
        "You resolve refund requests. Always look the order up before refunding, "
        "then refund the full amount."
    ),
)

# --- The integration: register the chowki-wrapped functions as tools --------------------
agent.tool_plain(lookup_order)
agent.tool_plain(issue_refund)


@agent.tool
def whoami(ctx: RunContext[SupportDeps]) -> str:
    """Return the reviewer this run escalates to. Shows chowki under a context tool."""
    return ctx.deps.reviewer_email


def durable_model(model: Any) -> Any:
    """Optional: memoise Pydantic AI's model requests and feed the budget guardrail."""
    inner = model.request

    @chowki.step(name="llm_turn")
    async def _turn(*args: Any, **kwargs: Any) -> Any:
        response = await inner(*args, **kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            report_usage(
                getattr(usage, "request_tokens", 0) or 0,
                getattr(usage, "response_tokens", 0) or 0,
            )
        return response

    model.request = _turn
    return model


@chowki.workflow
def refund_agent(order_id: str = "ord-4417") -> str:
    """A Pydantic AI run that suspends at a gate and resumes without re-doing work."""
    result = agent.run_sync(
        f"Customer wants a refund for order {order_id}. Look it up, then refund it.",
        deps=SupportDeps(reviewer_email="payments-review@example.com"),
    )
    return str(result.output)


def main() -> None:
    chowki.configure(
        db_path="./pydantic_ai_refund.db",
        gateway=ConsoleGateway(),
        resume_secret=b"pydantic-ai-32-byte-secret-value!",
    )

    run_id = "pydantic-ai-refund-1"
    try:
        print(refund_agent(order_id="ord-4417", run_id=run_id))
        return
    except chowki.WorkflowPaused as paused:
        print(
            f"\nPaused at {paused.step_id} before the transfer. Refunds so far: {executed_refunds}"
        )
        token = paused.token

    # EDIT rewrites run state before the workflow body replays — here, halving the refund.
    result = chowki.resume(
        run_id=run_id,
        token=token,
        decision=chowki.Decision.APPROVE,
    )
    print(f"\nResumed: {result.value}")
    print(f"Refunds executed: {executed_refunds}  <- one transfer, lookup_order memoised")


if __name__ == "__main__":
    main()

"""chowki + LangChain / LangGraph.

    pip install chowki "langchain>=1.0" langchain-anthropic

LangGraph owns the loop: it calls the model, routes tool calls to a tool node, and loops
until the model stops asking for tools. chowki sits underneath, at the tool boundary.

Decorator order is the whole integration::

    @tool            # outermost: LangChain builds the schema from the signature
    @chowki.step     # innermost: chowki intercepts every invocation
    def issue_refund(...): ...

`@chowki.step` uses `functools.wraps`, so `@tool` still sees the real name, docstring, and
type hints and generates the same JSON schema it would without chowki.

What that buys you, with the graph unchanged:

* The process dies mid-run. `chowki recover` + `chowki rerun` replays the graph, and every
  tool that already completed returns its recorded result instead of running again.
* The model decides to refund $240. `issue_refund` suspends the run and mints a signed
  token before the transfer, rather than after.
* An API key in the tool arguments is redacted before the snapshot is written.

Caveat worth stating plainly: chowki memoises what you decorate. LangGraph's own model
calls are not steps, so a resume re-issues them. Wrap the model call as shown in
`call_model_once` if re-billing those matters for your workload.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import chowki
from chowki.hitl.console import ConsoleGateway

sys.path.insert(0, str(Path(__file__).parent))

from _refund_domain import executed_refunds, issue_refund, lookup_order, report_usage

try:
    from langchain.agents import create_agent
    from langchain_core.tools import tool
except ImportError as exc:  # pragma: no cover - example dependency
    raise SystemExit(
        'This example needs LangChain 1.0+:  pip install "langchain>=1.0" langchain-anthropic'
    ) from exc


# --- The integration: one decorator line per tool --------------------------------------
lookup_order_tool = tool(lookup_order)
issue_refund_tool = tool(issue_refund)


def call_model_once(model: Any, messages: list[Any]) -> Any:
    """Optional: make the model call itself durable and budget-aware.

    LangGraph calls the model inside its own node, so it is outside chowki by default.
    Route the call through a step like this one — via a custom node or a
    `RunnableLambda` — and completed model calls stop being re-billed on resume.
    """

    @chowki.step(name="llm_turn")
    def _turn(payload: list[Any]) -> Any:
        response = model.invoke(payload)
        usage = getattr(response, "usage_metadata", None) or {}
        report_usage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        return response

    return _turn(messages)


@chowki.workflow
def refund_agent(order_id: str = "ord-4417") -> str:
    """A LangGraph agent whose tool calls are durable, gated, and redacted."""
    agent = create_agent(
        model="anthropic:claude-opus-5",
        tools=[lookup_order_tool, issue_refund_tool],
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Customer wants a refund for order {order_id}. "
                        "Look it up, then refund the full amount."
                    ),
                }
            ]
        }
    )
    return str(result["messages"][-1].content)


def main() -> None:
    chowki.configure(
        db_path="./langgraph_refund.db",
        gateway=ConsoleGateway(),
        resume_secret=b"langgraph-example-32-byte-secret!",
    )

    run_id = "langgraph-refund-1"
    try:
        print(refund_agent(order_id="ord-4417", run_id=run_id))
        return
    except chowki.WorkflowPaused as paused:
        print(
            f"\nPaused at {paused.step_id} before the transfer. Refunds so far: {executed_refunds}"
        )
        token = paused.token

    # A reviewer approves — from here, a web handler, or `chowki resume ...` in a shell.
    result = chowki.resume(run_id=run_id, token=token, decision=chowki.Decision.APPROVE)
    print(f"\nResumed: {result.value}")
    print(f"Refunds executed: {executed_refunds}  <- exactly one, and lookup_order was memoised")


if __name__ == "__main__":
    main()

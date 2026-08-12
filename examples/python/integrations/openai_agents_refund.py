"""chowki + the OpenAI Agents SDK.

    pip install chowki openai-agents

The Agents SDK is the thinnest of the popular harnesses: `@function_tool` turns type hints
and a docstring into the JSON schema, `Runner.run()` drives the loop, handoffs and
guardrails sit on top. It deliberately keeps no durable state between runs.

chowki supplies that half::

    @function_tool   # outermost: the SDK generates the schema from the signature
    @chowki.step     # innermost: chowki records, memoises, and can gate the call
    def issue_refund(...): ...

Because `@chowki.step` preserves the signature and docstring through `functools.wraps`,
the schema the model sees is byte-identical to the un-decorated version.

The SDK's own guardrails run *around* a turn — they can stop an agent, but they cannot
suspend a run for a day and resume it in a different process. `chowki.pause()` can: the
run is written to storage, a single-use HMAC token is minted, and `Runner.run()` raises
`WorkflowPaused` out to the caller.

Caveat: chowki memoises what you decorate. The SDK's model calls happen inside `Runner`,
so a resume re-issues them unless you route the model through a chowki step — see
`durable_model` below.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import chowki
from chowki.hitl.console import ConsoleGateway

sys.path.insert(0, str(Path(__file__).parent))

from _refund_domain import executed_refunds, issue_refund, lookup_order, report_usage

try:
    from agents import Agent, Runner, function_tool
except ImportError as exc:  # pragma: no cover - example dependency
    raise SystemExit(
        "This example needs the OpenAI Agents SDK:  pip install openai-agents"
    ) from exc


# --- The integration: @function_tool on the outside, @chowki.step underneath ------------
lookup_order_tool = function_tool(lookup_order)
issue_refund_tool = function_tool(issue_refund)


def durable_model(model: Any) -> Any:
    """Optional: make the SDK's model calls memoised and budget-aware.

    `Runner` calls the model itself, outside chowki's view. Passing a model whose
    `get_response` runs inside a `@chowki.step` means a resumed run replays recorded
    completions rather than paying for them twice.
    """
    inner = model.get_response

    @chowki.step(name="llm_turn")
    async def _turn(*args: Any, **kwargs: Any) -> Any:
        response = await inner(*args, **kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            report_usage(
                getattr(usage, "input_tokens", 0),
                getattr(usage, "output_tokens", 0),
            )
        return response

    model.get_response = _turn
    return model


refund_agent = Agent(
    name="Refund Specialist",
    instructions=(
        "You resolve refund requests. Look the order up before refunding. "
        "Refund the full amount unless the customer asks otherwise."
    ),
    tools=[lookup_order_tool, issue_refund_tool],
    model="gpt-5",
)


@chowki.workflow
async def run_refund_agent(order_id: str = "ord-4417") -> str:
    """An Agents SDK run that survives a crash and stops at a human gate."""
    result = await Runner.run(
        refund_agent,
        f"Customer wants a refund for order {order_id}. Look it up, then refund it.",
    )
    return str(result.final_output)


async def main() -> None:
    chowki.configure(
        db_path="./openai_agents_refund.db",
        gateway=ConsoleGateway(),
        resume_secret=b"openai-agents-32-byte-secret-ok!",
    )

    run_id = "openai-agents-refund-1"
    try:
        print(await run_refund_agent(order_id="ord-4417", run_id=run_id))
        return
    except chowki.WorkflowPaused as paused:
        print(
            f"\nPaused at {paused.step_id} before the transfer. Refunds so far: {executed_refunds}"
        )
        token = paused.token

    # `aresume` is the async twin of `chowki.resume` for async workflows.
    result = await chowki.aresume(run_id=run_id, token=token, decision=chowki.Decision.APPROVE)
    print(f"\nResumed: {result.value}")
    print(f"Refunds executed: {executed_refunds}  <- lookup_order was memoised, not re-run")


if __name__ == "__main__":
    asyncio.run(main())

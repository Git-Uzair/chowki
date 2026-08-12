"""chowki with no framework at all — a hand-written Claude tool loop.

    pip install chowki anthropic

Plenty of production agents are a `while` loop around the Messages API. No graph, no crew,
no runner. That case is the clearest demonstration of what chowki actually does, because
there is no framework in the way: you own the loop, so you can put *both* halves of the
agent inside chowki steps.

    llm_turn      @chowki.step                 the model call itself -> memoised
    lookup_order  @chowki.step                 safe tool
    issue_refund  @chowki.step(idempotent=False)   pauses -> re-enterable
      -> _post_refund  @chowki.step            the transfer, exactly-once

That is the version of the story with no caveat attached. Kill this process mid-run, then
`chowki recover` and `chowki rerun`: completed model turns replay from storage and are
never re-billed, completed tools return their recorded results, and the run picks up at the
first thing that had not finished.

Model IDs and the request shape here follow the current Messages API: `claude-opus-5`,
adaptive thinking on by default, and `stop_reason` checked before reading content.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import chowki
from chowki.guardrails import GuardrailConfig
from chowki.hitl.console import ConsoleGateway

sys.path.insert(0, str(Path(__file__).parent))

from _refund_domain import executed_refunds, issue_refund, lookup_order

try:
    import anthropic
except ImportError as exc:  # pragma: no cover - example dependency
    raise SystemExit("This example needs the Anthropic SDK:  pip install anthropic") from exc

MODEL = "claude-opus-5"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "lookup_order",
        "description": "Look up an order by its id. Returns customer and amount.",
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "e.g. ord-4417"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "issue_refund",
        "description": (
            "Refund a customer for an order. Refunds over $100 require human approval, "
            "which this tool obtains on its own — call it and it will handle the gate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "description": "Amount in USD"},
            },
            "required": ["order_id", "amount"],
        },
    },
]

_DISPATCH = {"lookup_order": lookup_order, "issue_refund": issue_refund}


@chowki.step(name="llm_turn")
def llm_turn(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """One model call, recorded as a durable step.

    This is the line that makes a resumed run free: the turn is memoised on its arguments,
    so replaying the loop returns the recorded response instead of paying for it again.
    Usage is reported here so the budget guardrail sees every token the agent spends.
    """
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=16_000,
        tools=TOOLS,
        messages=messages,  # type: ignore[arg-type]
    )
    chowki.report_usage(
        chowki.Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
    )
    return response.model_dump()


@chowki.workflow
def refund_agent(order_id: str = "ord-4417") -> str:
    """A plain Messages API tool loop, made durable and human-gated end to end."""
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Customer wants a refund for order {order_id}. "
                "Look it up, then refund the full amount."
            ),
        }
    ]

    for _ in range(10):  # bound the loop; chowki's loop detector is the backstop
        response = llm_turn(messages)
        stop_reason = response.get("stop_reason")

        if stop_reason == "refusal":
            return "The model declined this request."
        if stop_reason == "end_turn":
            return "".join(
                block["text"] for block in response["content"] if block.get("type") == "text"
            )
        if stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response["content"]})
            continue

        messages.append({"role": "assistant", "content": response["content"]})
        results: list[dict[str, Any]] = []
        for block in response["content"]:
            if block.get("type") != "tool_use":
                continue
            fn = _DISPATCH[block["name"]]
            # Tool calls are chowki steps: memoised on replay, and `issue_refund`
            # suspends the whole run here if the amount needs a human.
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": str(fn(**block["input"])),
                }
            )
        messages.append({"role": "user", "content": results})

    return "Gave up after 10 turns."


def main() -> None:
    chowki.configure(
        db_path="./anthropic_refund.db",
        gateway=ConsoleGateway(),
        resume_secret=b"anthropic-loop-32-byte-secret-ok!",
        guardrails=GuardrailConfig(max_token_budget=200_000, soft_budget_threshold=0.8),
    )

    run_id = "anthropic-refund-1"
    try:
        print(refund_agent(order_id="ord-4417", run_id=run_id))
        return
    except chowki.WorkflowPaused as paused:
        print(
            f"\nPaused at {paused.step_id} before the transfer. Refunds so far: {executed_refunds}"
        )
        token = paused.token

    result = chowki.resume(run_id=run_id, token=token, decision=chowki.Decision.APPROVE)
    print(f"\nResumed: {result.value}")
    print(f"Refunds executed: {executed_refunds}")
    print("Model turns before the gate were replayed from storage, not re-billed.")


if __name__ == "__main__":
    main()

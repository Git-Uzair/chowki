"""The shared refund domain every integration example wires into its framework.

Keeping the chowki-decorated tools in one place makes the per-framework files show the
only thing that actually differs: how that framework is handed a tool, and where its
model call reports usage. Nothing here imports a framework.

The three-function shape is the important part, and it is load-bearing:

    lookup_order          @chowki.step                 safe, memoised
    issue_refund          @chowki.step(idempotent=False)   pauses -> re-enterable
      -> _post_refund     @chowki.step                 the side effect, exactly-once

A step whose body can reach `chowki.pause()` MUST be declared `idempotent=False`. The
idempotency claim is taken before the body runs and a pause does not release it, so a
default step that pauses raises `ChowkiStorageError` when the resume re-enters it. Keeping
the real side effect in a nested default step gives you both halves: a gate you can
re-enter and a transfer that can only happen once.
"""

from __future__ import annotations

from typing import Any

import chowki

#: Stand-in for a payments backend. A real one would be an HTTP call inside `_post_refund`.
_ORDERS: dict[str, dict[str, Any]] = {
    "ord-4417": {"order_id": "ord-4417", "customer": "ada@example.com", "amount": 240.00},
    "ord-9902": {"order_id": "ord-9902", "customer": "grace@example.com", "amount": 18.50},
}

#: Every refund actually executed in this process. The integration examples print it to
#: show that a resume does not double-refund.
executed_refunds: list[str] = []


@chowki.step
def lookup_order(order_id: str) -> dict[str, Any]:
    """Look up an order by its id.

    Args:
        order_id: The order identifier, e.g. "ord-4417".
    """
    order = _ORDERS.get(order_id)
    if order is None:
        return {"error": f"no such order: {order_id}"}
    return dict(order)


@chowki.step
def _post_refund(order_id: str, amount: float) -> str:
    """Perform the irreversible transfer. Idempotency-claimed, so it happens once."""
    executed_refunds.append(order_id)
    return f"refunded ${amount:.2f} on {order_id}"


@chowki.step(idempotent=False)
def issue_refund(order_id: str, amount: float) -> str:
    """Refund a customer. Refunds over $100 require human approval.

    Args:
        order_id: The order to refund.
        amount: Amount in USD.
    """
    if amount > 100:
        chowki.pause(
            reason=f"Refund of ${amount:.2f} on {order_id} needs approval",
            payload={"order_id": order_id, "amount": amount},
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
        )
    return _post_refund(order_id, amount)


def report_usage(input_tokens: int, output_tokens: int) -> None:
    """Feed a model call's usage into the run's budget guardrail.

    Every framework surfaces token counts somewhere; the per-framework files show where.
    Once reported, `GuardrailConfig(max_token_budget=...)` can warn or auto-pause the run.
    """
    chowki.report_usage(chowki.Usage(input_tokens=input_tokens, output_tokens=output_tokens))

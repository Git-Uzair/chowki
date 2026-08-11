# chowki

`chowki` is an agent-native, in-process control plane and durable execution engine designed for Python and polyglot environments. It embeds directly into existing application codebases via lightweight decorators, providing zero-infrastructure warm resume, automated secret redaction, and active guardrails.

**Planning anything? Start at [`docs/features.md`](docs/features.md)** — the complete language-agnostic feature catalog and Python/Node parity matrix, with the plan-generation workflow written into its header. The current work-in-progress plan is [`docs/plans/02-release.md`](docs/plans/02-release.md) (the path to publishing v0.1); phase scope lives in [`docs/plans/00-roadmap.md`](docs/plans/00-roadmap.md).

## Installation

```bash
uv add chowki
```

## Quickstart

<!-- kept in sync with examples/python/quickstart.py (Task 22) -->
```python
"""Chowki Quickstart Example: workflow pause, console output, and warm resume.

Note:
    Every side effect in a Chowki workflow must live inside a `@chowki.step`.
    Because `resume()` re-executes the workflow function body from the top,
    any side effect outside a `@chowki.step` will be re-executed on warm resume.
"""

from __future__ import annotations

from typing import Any, cast

import chowki
from chowki.hitl.console import ConsoleGateway
from chowki.storage.memory import MemoryStorage

# Configure chowki with MemoryStorage and ConsoleGateway for human-in-the-loop notifications.
engine = chowki.configure(
    storage=MemoryStorage(),
    gateway=ConsoleGateway(),
    resume_secret=b"a-32-byte-secret-for-signing-token!",
)


@chowki.step
def prepare_proposal(amount: int, recipient: str) -> dict[str, Any]:
    return {"recipient": recipient, "amount": amount, "status": "draft"}


@chowki.step
def send_payment(proposal: dict[str, Any]) -> str:
    return f"Paid {proposal['amount']} to {proposal['recipient']}"


@chowki.workflow
def payment_workflow(amount: int = 500, recipient: str = "unverified@example.com") -> str:
    # All side effects must live inside @chowki.step functions because resume()
    # re-executes the workflow function body from the top on warm resume.
    proposal = prepare_proposal(amount, recipient)
    chowki.current_run().state["proposal"] = proposal

    # Pause workflow for human approval when amount > 100
    if amount > 100:
        chowki.pause(
            reason="High-value payment requires review",
            payload=proposal,
            permitted_actions=("APPROVE", "REJECT", "EDIT"),
        )

    prop = cast(dict[str, Any], chowki.current_run().state["proposal"])
    return send_payment(prop)


def main() -> None:
    run_id = "quickstart-run-1"
    token = None

    try:
        payment_workflow(amount=500, recipient="unverified@example.com", run_id=run_id)
    except chowki.WorkflowPaused as exc:
        token = exc.token
        print(f"\n[Quickstart] Caught WorkflowPaused for run {exc.run_id}!")

    if token is None:
        raise RuntimeError("Workflow should have paused")

    # Resume the workflow with an EDIT decision replacing the recipient
    result = chowki.resume(
        run_id=run_id,
        token=token,
        decision=chowki.Decision.EDIT,
        patch=[{"op": "replace", "path": "/proposal/recipient", "value": "verified@example.com"}],
        workflow_fn=payment_workflow,
        engine=engine,
    )

    print(f"\n[Quickstart] Resume result: {result.value}")


if __name__ == "__main__":
    main()
```

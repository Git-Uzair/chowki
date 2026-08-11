# chowki

[![Python Versions](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://github.com/Git-Uzair/chowki)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`chowki` is an agent-native, in-process control plane and durable execution engine designed for Python and polyglot environments. It embeds directly into existing application codebases via lightweight decorators (`@chowki.step`, `@chowki.workflow`), providing zero-infrastructure warm resume, automated multi-tier secret redaction, token budget guardrails, and HMAC-signed Human-in-the-Loop (HITL) approval gates.

**Planning anything? Start at [`docs/features.md`](docs/features.md)** — the complete language-agnostic feature catalog and Python/Node parity matrix, with the plan-generation workflow written into its header. Phase scope lives in [`docs/plans/00-roadmap.md`](docs/plans/00-roadmap.md).

## Key Features

- **Zero-Infrastructure Warm Resume**: Re-executes workflow bodies from the top with step memoisation on warm resume or process crash recovery without external orchestrator workers.
- **Automated Multi-Tier Redaction**: Scans and redacts credentials, secret keys, and high-entropy tokens across step arguments, state snapshots, and audit logs using stable HMAC placeholders.
- **Active Guardrails & Loop Breakers**: Protects against runaway agent loops (3-tier detection: tool-call hashes, Levenshtein text similarity, delegation graph cycles) and token budget overruns with configurable auto-pause actions.
- **Human-in-the-Loop Approval Gates**: Cryptographically signs pause requests with single-use HMAC-SHA256 tokens and nonces, supporting `APPROVE`, `REJECT`, `EDIT` state patches, and `ESCALATE` decisions.
- **Zero-Dependency CLI & Web App Embedding**: Operate runs via the `chowki` console script or embed approval handlers into FastAPI / Flask applications.

## User Guide & Documentation

Explore the **[Chowki User Guide](docs/user-guide/index.md)** for detailed tutorials and architecture reference:

- **[Core Concepts](docs/user-guide/concepts.md)** — Runs, steps, state, base/delta snapshots, and engine architecture.
- **[Warm Resume & Durable Execution](docs/user-guide/warm-resume.md)** — Top-down function re-execution, step memoisation, **Rule R4**, crash recovery, and step overrides.
- **[Guardrails & Loop Breakers](docs/user-guide/guardrails.md)** — Token/cost budgets, loop detection tiers (`record_text`, `record_transition`), and OpenAI/Anthropic recipes.
- **[Human-in-the-Loop (HITL)](docs/user-guide/hitl.md)** — Pause gates, HMAC tokens, decisions (`APPROVE`, `REJECT`, `EDIT`, `ESCALATE`), and CLI walkthrough.
- **[Configuration & Security](docs/user-guide/configuration.md)** — `ChowkiConfig`, `resume_secret`, AES-256-GCM encryption at rest, storage paths, and multi-tenancy.
- **[Limits & Operational Boundaries](docs/user-guide/limits.md)** — Single-writer constraints, SQLite concurrency, non-UTF-8 redaction, and defense-in-depth guarantees.
- **[Resuming Workflows in Production](docs/user-guide/resuming-in-production.md)** — FastAPI / Flask integration, async background tasks, and HTTP status mappings.

## Installation

`chowki` is not published to PyPI yet — PyPI publication happens when a maintainer pushes the `v0.1.0` tag. Until then, install from a clone of this repository:

```bash
git clone https://github.com/Git-Uzair/chowki.git
cd chowki
pip install ./python/chowki
```

To work on `chowki` itself, sync the `uv` workspace instead:

```bash
uv sync --all-extras --dev
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
        engine=engine,
    )

    print(f"\n[Quickstart] Resume result: {result.value}")


if __name__ == "__main__":
    main()
```

## Showcase: The Zero-Waste Agent (`examples/python/agent_review.py`)

Check out [`examples/python/agent_review.py`](examples/python/agent_review.py) for a complete, runnable showcase demonstrating Chowki's core capabilities in action:

1. **Self-contained LLM tool-use agent:** Runs out of the box with a deterministic fake LLM (no external API keys needed) and includes a clearly marked 5-line snippet to swap in OpenAI/Anthropic SDKs.
2. **Active token budget guardrails:** Reports token usage via `chowki.report_usage()` and triggers soft warnings when approaching budget limits.
3. **Human-in-the-Loop approval gate:** Automatically pauses at `chowki.pause()` before dangerous side-effect tools (like sending email).
4. **Interactive state patch on resume:** Resume via CLI with an `EDIT` decision to patch draft parameters (e.g. updating an email recipient).
5. **Zero-waste crash recovery:** Simulate a mid-run process crash with `--crash-after 3`, then recover (`chowki recover`) and rerun (`chowki rerun`). Previous LLM steps are memoised and skipped — **0 duplicate LLM calls or wasted tokens**.

Run the showcase agent:

```bash
uv run python examples/python/agent_review.py
```

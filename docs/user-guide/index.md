# Chowki User Guide

`chowki` is an in-process agent state preservation, guardrail, and warm resume library for Python. It turns LLM agent workflows into durable, inspectable, human-gated executions with zero required external infrastructure.

---

## User Guide Contents

1. **[Core Concepts](concepts.md)**
   Understand runs, steps, state, base/delta snapshots, and the execution engine mental model.
2. **[Warm Resume & Durable Execution](warm-resume.md)**
   Top-down function re-execution, step memoisation, **the R4 rule** (every side effect in a `@chowki.step`), step identity, crash recovery (`recover_runs` / `rerun`), and step override matrices.
3. **[Guardrails & Loop Breakers](guardrails.md)**
   Token and cost budgets, loop detection tiers (`record_text`, `record_transition`), OpenAI/Anthropic usage tracking recipes, and auto-pause behavior.
4. **[Human-in-the-Loop (HITL)](hitl.md)**
   Pause gates, HMAC resume tokens, decisions (`APPROVE`, `REJECT`, `EDIT`, `ESCALATE`), audit logging, console gateway, and CLI walkthrough.
5. **[Configuration & Security](configuration.md)**
   `ChowkiConfig` field reference, `resume_secret` in production, envelope encryption (`CHOWKI_MASTER_KEY`), storage paths, and multi-tenancy.
6. **[Limits & Operational Boundaries](limits.md)**
   Single-process / single-writer constraints, SQLite concurrency, non-UTF-8 redaction behavior, `<TypeName>` argument hash collapse, and defense-in-depth security guarantees.
7. **[Resuming Workflows in Production](resuming-in-production.md)**
   Web app integration, FastAPI and Flask recipes, async background resumes, HTTP status mappings, and security best practices.
8. **[Launch Checklist & Positioning](launch-checklist.md)**
   Launch checklist, key differentiators against Temporal/LangGraph, zero-waste kill-demo narration, CLI approval flow, and secret redaction.

---

## Quickstart

Install `chowki` from a clone of the repository (PyPI publication is pending the `v0.1.0` tag):

```bash
pip install ./python/chowki
```

Define a workflow with durable steps and guardrails:

```python
import chowki


@chowki.step
def fetch_data(query: str) -> dict[str, str]:
    return {"query": query, "result": "raw content"}


@chowki.step
def process_data(data: dict[str, str]) -> str:
    return data["result"].upper()


@chowki.workflow
def my_workflow(query: str = "chowki guide") -> str:
    raw = fetch_data(query)
    processed = process_data(raw)
    return processed


if __name__ == "__main__":
    result = my_workflow("chowki guide")
    print("Workflow result:", result)
```

---

## Navigation & Next Steps

Start by reading **[Core Concepts](concepts.md)** to learn how `chowki` preserves workflow state during execution.

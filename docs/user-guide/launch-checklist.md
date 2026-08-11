# Chowki Launch Checklist & Positioning

This document outlines the launch checklist, key positioning points, and the advertising copy skeleton for `chowki` v0.1.0.

---

## Launch Positioning & Core Value Proposition

`chowki` is an in-process state preservation, guardrail, and warm resume library for Python LLM agents.

### Key Differentiators

1. **Zero-Infrastructure Positioning against Temporal & LangGraph**
   - **No External Infrastructure:** Unlike Temporal (which requires running dedicated server clusters, workers, and databases) or complex multi-agent frameworks like LangGraph, `chowki` is a lightweight, pure Python library embedded directly into your application process.
   - **Zero Operational Overhead:** State is persisted to a local SQLite database or in-memory store synchronously before steps return. No external message queues, state stores, or sidecar containers are needed.

2. **Always-On Secret Redaction (One-Liner)**
   - Automatic, multi-tier redaction (regex pattern matching for API keys/tokens plus Shannon entropy scanning for random high-entropy credentials) is built-in and always enabled on all persisted state.
   - Credentials are replaced with HMAC placeholders (`[REDACTED:kind:hash]`) derived under a stable per-deployment key before hitting storage or logs.

3. **Honest Single-Process Boundary**
   - **Single-Writer Constraint:** `chowki` is designed for single-process, single-writer per workflow run execution.
   - **Process Boundaries:** Concurrent multi-process workers attempting simultaneous writes to the same run context are intentionally unsupported in SQLite storage (governed by single-writer file locks). For distributed multi-process scale-out, multi-region workers, or cluster leasing, see the Phase 5 roadmap (PostgreSQL/Redis adapters).

---

## Showcase Narratives & Advertising Copy Skeleton

### 1. The Kill-Demo & GIF Narration (`examples/python/agent_review.py`)

The flagship showcase demonstrates zero-waste crash recovery for long-running LLM agents:

1. **Execution Starts:** An agent executes tools (`search`, `read_file`) and accumulates intermediate LLM results.
2. **Budget Auto-Pause or Human Approval Gate:** The agent pauses when hitting a budget threshold or entering a human-gated step (e.g., `send_email`).
3. **Mid-Run Process Termination:** The application process is killed mid-run (`SIGKILL` or process crash).
4. **Zero-Waste Resume:** Calling `chowki recover` followed by `chowki rerun` (or programmatically via `chowki.recover_runs` and `chowki.rerun`) restores the exact run state and re-executes from the last completed step. Already completed LLM API calls and steps are **never re-executed or re-billed**.

### 2. CLI Approval & Inspection Flow

Inspect, review, and resume suspended workflow runs directly from the terminal or admin interface:

```bash
# List all paused or incomplete workflow runs
chowki runs list

# Inspect full run state, history, and audit log without disturbing live state
chowki runs show <run_id>

# Resume a paused run with human approval or state patch (passing -m <module> to load workflow registry)
chowki -m my_app resume <run_id> --token <resume_token> --decision APPROVE

# Rerun a recovered run
chowki -m my_app rerun <run_id>

# Re-issue a lost or burnt token for a paused run
chowki reissue-token <run_id>
```

### 3. Next Showcase Beat: Slack Socket Mode

Phase 4 opens with native Slack Socket Mode channel gateway integration, enabling human-in-the-loop approval gates directly inside Slack without requiring public webhook ingress infrastructure.

---

## Pre-Launch Checklist

- [x] All Phase 1 and Phase 2 unit and integration tests passing (`pytest python/chowki/tests`)
- [x] Type safety clean across codebase (`pyright` strict and `mypy` strict)
- [x] Code style and formatting clean (`ruff check .` and `ruff format --check .`)
- [x] Local CI script passing (`python scripts/ci_local.py`)
- [x] User guide updated with concept guides, API specs, and production embedding recipes
- [x] Wheel built and verified via wheel smoke test (`uv build`)

# Chowki Python Examples

This directory contains runnable python examples demonstrating `chowki` features.

## Examples

### Quickstart (`quickstart.py`)

A standalone, zero-dependency script demonstrating:
1. Workflow and step decoration (`@chowki.step`, `@chowki.workflow`)
2. Human-in-the-Loop workflow suspension (`chowki.pause`) with console notifications (`ConsoleGateway`)
3. Capturing `chowki.WorkflowPaused` and single-use resume tokens
4. Applying RFC 6902 JSON Patch state modifications during warm resume (`chowki.resume`)

Run with:

```bash
uv run python examples/python/quickstart.py
```

### Showcase Agent (`agent_review.py`)

A full LLM tool-use agent demonstrating Chowki's core control-plane features:
1. **Tool-use loop with deterministic fake LLM** (plus 5-line swap-in for real OpenAI/Anthropic clients)
2. **Budget tracking & soft warnings** (`chowki.report_usage`)
3. **Approval gate** (`chowki.pause`) before dangerous tool execution (`send_email`)
4. **State editing on resume** (RFC 6902 JSON Patch changing email recipient)
5. **Zero-waste crash recovery**: simulate a mid-run crash with `--crash-after 3`, then recover (`chowki recover`) and rerun (`chowki rerun`) without repeating completed LLM calls.

Run in normal mode:

```bash
uv run python examples/python/agent_review.py
```

`--crash-after N` simulates a process dying mid-run after step N (1, 2, or 3; `CHOWKI_CRASH_AFTER=N`
does the same). There are two ways to come back from that crash.

**Automatic, in-script recovery** — the script recovers and reruns itself:

```bash
uv run python examples/python/agent_review.py --crash-after 1
```

It prints `Total LLM calls executed: 2`: the step that completed before the crash is
memoised, so the rerun never repeats its LLM call.

**Manual, operator-driven recovery** — `--no-auto-recover` exits 1 and leaves the run stalled
in `RUNNING` status, exactly as a `kill -9` would, so you can drive the recovery yourself:

```bash
# 1. Crash after the post-approval step 3, leaving the run stalled for an operator
uv run python examples/python/agent_review.py --crash-after 3 --no-auto-recover

# 2. Recover the stalled run back to PENDING status
uv run chowki --db ./chowki.db -m examples.python.agent_review recover

# 3. Rerun the recovered run — completed LLM steps are skipped and not re-executed!
uv run chowki --db ./chowki.db -m examples.python.agent_review rerun showcase-agent-run-1
```

Step 3 prints `Reran showcase-agent-run-1: Email sent to security-team@example.com ...` — a
fresh process, zero repeated LLM calls, and the approval decision replayed from the audit log.
Crash before the approval gate instead (`--crash-after 1 --no-auto-recover`) and the rerun stops
at that gate, reporting `run showcase-agent-run-1 paused at pause#3`; resume it with
`chowki ... resume` (see `--help`) or let the automatic mode above handle it.

## Best Practices

- **Workflow Side Effects Rule**: Every side effect in a Chowki workflow must live inside a `@chowki.step`. Because `resume()` re-executes the workflow function body from the top, any side effect outside a `@chowki.step` will be re-executed on warm resume.

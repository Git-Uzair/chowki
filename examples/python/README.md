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

Run with crash simulation & CLI recovery:

```bash
# 1. Run agent with simulated crash after post-pause step 3
uv run python examples/python/agent_review.py --crash-after 3

# 2. Recover stalled run back to PENDING status
uv run chowki --db ./chowki.db -m examples.python.agent_review recover

# 3. Rerun recovered run — completed LLM steps are skipped and not re-executed!
uv run chowki --db ./chowki.db -m examples.python.agent_review rerun showcase-agent-run-1
```

## Best Practices

- **Workflow Side Effects Rule**: Every side effect in a Chowki workflow must live inside a `@chowki.step`. Because `resume()` re-executes the workflow function body from the top, any side effect outside a `@chowki.step` will be re-executed on warm resume.

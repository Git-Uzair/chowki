# Guardrails & Loop Breakers

`chowki` includes built-in guardrails to protect LLM agents against infinite loops, runaway token consumption, and unexpected API costs.

---

## Default Guardrail Configuration

Guardrail behavior is configured via `GuardrailConfig`. Default thresholds:

| Configuration Field | Default Value | Description |
|---|---|---|
| `max_steps_per_run` | `25` | Step executions per run; the step that exceeds it raises `InfiniteLoopDetected`. |
| `tool_loop_window_size` | `5` | Sliding window size for step/tool repetition tracking. |
| `tool_loop_max_repeats` | `3` | Identical executions within the window that trigger the break (the 3rd raises). |
| `semantic_loop_warn_threshold` | `0.85` | Normalized Levenshtein similarity threshold for semantic loop warning. |
| `semantic_loop_pause_threshold` | `0.95` | Similarity threshold for triggering infinite loop auto-pause. |
| `semantic_loop_consecutive` | `3` | Consecutive near-duplicate texts required to trigger loop detection. |
| `soft_budget_threshold` | `0.80` | Soft budget ratio (80%) that triggers a log warning. |
| `max_token_budget` | `None` | Hard ceiling for billable tokens per run (input + output + reasoning; cached input tokens excluded). |
| `max_cost_usd` | `None` | Hard ceiling for total USD cost per run. |
| `hard_budget_action` | `"PAUSE"` | Action when hard budget ceiling is reached (`"PAUSE"` or `"ABORT"`). |
| `enabled` | `True` | Master toggle for guardrail enforcement. |

---

## Loop Detection Tiers

`chowki` implements multi-tiered loop detection to catch repeating agent patterns:

### Tier 1: Identical Tool Call Repetition
Every `@chowki.step` that actually executes its body — memoised replays do not count — is recorded by name and argument hash in a sliding window (`tool_loop_window_size`). A step is chowki's unit of tool execution, so wrap each tool call in one. When the same signature appears `tool_loop_max_repeats` times inside the window, `InfiniteLoopDetected` is raised and the run auto-pauses.

### Tier 2: Semantic Prompt / Response Loops (`record_text`)
Feed LLM prompt or response text to `chowki.record_text()`:

```python
import chowki


@chowki.step
def call_agent_llm(prompt: str) -> str:
    response_text = "Generated LLM response"
    chowki.record_text(response_text)
    return response_text
```

Once the last `semantic_loop_consecutive` texts are in hand, each neighbouring pair is compared: if every similarity reaches `semantic_loop_pause_threshold`, `chowki` raises `InfiniteLoopDetected`; if every similarity reaches only `semantic_loop_warn_threshold`, it logs a `chowki_semantic_loop_warning` and lets the run continue.

### Tier 3: Agent Transition Cycles (`record_transition`)
Track multi-agent delegation edges using `chowki.record_transition(src, dst)`:

```python
import chowki


@chowki.step
def delegate_agent(from_agent: str, to_agent: str) -> None:
    chowki.record_transition(from_agent, to_agent)
```

Only edges recorded at least twice enter the graph — a one-off hand-off is not a loop. A cycle among those repeated edges raises `InfiniteLoopDetected`, which auto-pauses the run.

---

## Budget Tracking & Provider Recipes

Report token usage or cost to the active run using `chowki.report_usage()`.

### OpenAI Usage Recipe

Map OpenAI API response usage dictionaries directly to `chowki.report_usage`:

```python
from typing import Any
import chowki
from chowki.types import Usage


def handle_openai_response(response: dict[str, Any]) -> str:
    usage = response.get("usage", {})
    chowki.report_usage(
        Usage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            cost_usd=0.0015 * (usage.get("total_tokens", 0) / 1000.0),
        )
    )
    return response["choices"][0]["message"]["content"]
```

### Anthropic Usage Recipe

Map Anthropic Messages API response usage dictionaries directly to `chowki.report_usage`:

```python
from typing import Any
import chowki
from chowki.types import Usage


def handle_anthropic_response(response: dict[str, Any]) -> str:
    usage = response.get("usage", {})
    chowki.report_usage(
        Usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cost_usd=0.003 * (usage.get("input_tokens", 0) / 1000.0),
        )
    )
    return response["content"][0]["text"]
```

---

## Auto-Pause & Resume-After-Raise Flow

When a budget ceiling or infinite loop is detected:
1. `chowki` marks the run status as `PAUSED` and stores the pause request (step, reason, permitted actions, reviewers) on the run record.
2. A resume token is minted: its claims (run ID, step ID, permitted actions, nonce, expiry) are HMAC-SHA256 signed with `resume_secret` and handed to the caller and the gateway. The token itself is **never stored** — verification is stateless, and storage only records the token's nonce when it is consumed, which is what makes the token single-use.
3. `chowki` raises `WorkflowPaused` (or `BudgetExceeded` / `InfiniteLoopDetected`).

Because the token is not persisted, a lost token cannot be read back out of the database — mint a fresh one with `chowki.reissue_token(run_id)`.

To resume a run after an auto-pause:

```python
import chowki
from chowki.types import Decision

# Resume after expanding budget or resolving loop
res = chowki.resume(
    run_id="run-uuid-123",
    token="issued-pause-token",
    decision=Decision.APPROVE,
    note="Budget expanded by operator",
)
```

---

## What Can Go Wrong

1. **Forgetting to Call `report_usage`:** Token and cost budgets only enforce limits if LLM responses report their usage to `chowki.report_usage()`.
2. **Ignoring `WorkflowPaused` Exceptions:** In custom HTTP workers, failing to catch `WorkflowPaused` causes HTTP 500 errors instead of returning 202 Accepted with a pause token.
3. **Setting `hard_budget_action="ABORT"` Unintentionally:** With `"ABORT"` a budget breach propagates `BudgetExceeded` and the run is recorded `FAILED` — no pause request, no resume token, so there is no human gate to approve. The run can then only be re-driven with `rerun()`, after raising the budget that stopped it.

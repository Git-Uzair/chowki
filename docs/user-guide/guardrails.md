# Guardrails & Loop Breakers

`chowki` includes built-in guardrails to protect LLM agents against infinite loops, runaway token consumption, and unexpected API costs.

---

## Default Guardrail Configuration

Guardrail behavior is configured via `GuardrailConfig`. Default thresholds:

| Configuration Field | Default Value | Description |
|---|---|---|
| `max_steps_per_run` | `25` | Maximum allowed step executions before triggering auto-pause. |
| `tool_loop_window_size` | `5` | Sliding window size for tool call repetition tracking. |
| `tool_loop_max_repeats` | `3` | Maximum identical tool executions within the window before breaking. |
| `semantic_loop_warn_threshold` | `0.85` | Normalized Levenshtein similarity threshold for semantic loop warning. |
| `semantic_loop_pause_threshold` | `0.95` | Similarity threshold for triggering infinite loop auto-pause. |
| `semantic_loop_consecutive` | `3` | Consecutive near-duplicate texts required to trigger loop detection. |
| `soft_budget_threshold` | `0.80` | Soft budget ratio (80%) that triggers a log warning. |
| `max_token_budget` | `None` | Hard ceiling for total tokens consumed per run (inputs + outputs). |
| `max_cost_usd` | `None` | Hard ceiling for total USD cost per run. |
| `hard_budget_action` | `"PAUSE"` | Action when hard budget ceiling is reached (`"PAUSE"` or `"ABORT"`). |
| `enabled` | `True` | Master toggle for guardrail enforcement. |

---

## Loop Detection Tiers

`chowki` implements multi-tiered loop detection to catch repeating agent patterns:

### Tier 1: Identical Tool Call Repetition
`chowki` tracks tool execution signatures within a sliding window (`tool_loop_window_size`). Repeating identical calls beyond `tool_loop_max_repeats` triggers an auto-pause.

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

If consecutive response texts exceed `semantic_loop_pause_threshold` similarity over `semantic_loop_consecutive` turns, `chowki` raises `InfiniteLoopDetected`.

### Tier 3: Agent Transition Cycles (`record_transition`)
Track multi-agent delegation edges using `chowki.record_transition(src, dst)`:

```python
import chowki


@chowki.step
def delegate_agent(from_agent: str, to_agent: str) -> None:
    chowki.record_transition(from_agent, to_agent)
```

Cycle detection algorithms evaluate the delegation graph. Repeated cycles trigger auto-pause.

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
1. `chowki` marks the run status as `PAUSED`.
2. A single-use HMAC pause token is issued and persisted in storage.
3. `chowki` raises `WorkflowPaused` (or `BudgetExceeded` / `InfiniteLoopDetected`).

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
3. **Setting `hard_budget_action="ABORT"` Unintentionally:** Setting hard action to `"ABORT"` permanently terminates the run when limits are hit, making warm resume impossible.

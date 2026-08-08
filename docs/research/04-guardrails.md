# Loop and Anomaly Detection for Autonomous AI Agents

**Project:** `chowki`  
**Date:** 2026-08-08  
**Scope:** Loop detection algorithms, token and monetary cost enforcement, error-class taxonomy & actions, prior art analysis, and recommended guardrail defaults for `chowki`.

---

## 1. Repeated-Call & Cycle Detection

### 1.1 Failure Modes in Autonomous Agent Execution
Autonomous AI agents executing multi-step workflows or tool invocation loops are susceptible to three distinct classes of cyclic trajectory failure:

1. **Infinite Loops:** An agent entering an unbounded execution cycle where it repeatedly executes the same task step or thought cycle without reaching a terminal condition or advancing task state.
2. **Tool Ping-Pong:** Multi-agent or single-agent delegation loops where agent/tool A delegates to agent/tool B, which immediately delegates back to A ($A \rightarrow B \rightarrow A \rightarrow B$), generating infinite conversational/execution turns without making progress on the user goal.
3. **Duplicate Parameter Invocations:** An agent repeatedly invoking a single tool using identical or trivially altered parameter payloads (e.g., repeatedly calling a search tool with the exact same query when previous invocations returned empty or unexpected results).

* [Source: https://runguard.dev/blog/autogen-cost-control-loop-detection.html (Accessed: 2026-08-08)]
* [Source: https://joshuaopolko.com/crewai-setup-production-guide/ (Accessed: 2026-08-08)]

---

### 1.2 Algorithmic Approaches for Loop & Cycle Detection

#### 1. Windowed Hash Sets (Exact Match Detection)
* **Mechanism:** Maintains a sliding window of size $k$ (e.g., $k=5$) storing canonicalized hashes of step execution payloads: `SHA-256(tool_name + canonical_json(kwargs))`. If the frequency of a single hash in the sliding window exceeds a threshold (e.g., 3 occurrences in 5 steps), an `InfiniteLoopDetected` error is raised.
* **Pros:** Ultra-low computational overhead ($O(1)$ lookup complexity), deterministic execution, zero false positives for byte-identical invocations.
* **Cons:** Vulnerable to minor formatting variations, timestamp shifts, whitespace differences, or trivial argument mutations introduced by non-deterministic LLMs.
* [Source: https://runguard.dev/blog/autogen-cost-control-loop-detection.html (Accessed: 2026-08-08)]

#### 2. Levenshtein Distance on Prompt & Tool History
* **Mechanism:** Computes character/token-level normalized Levenshtein edit distance between consecutive prompt representations or stringified tool output histories:
  $$\text{Similarity}(S_1, S_2) = 1 - \frac{\text{Levenshtein}(S_1, S_2)}{\max(|S_1|, |S_2|)}$$
  If similarity exceeds a threshold (e.g., $>0.85$) across $N$ consecutive turns, a near-duplicate loop is detected.
* **Pros:** Detects minor wording variations, formatting noise, and slight parameter rephrasings without requiring embedding model calls.
* **Cons:** $O(N \cdot M)$ string comparison latency on large context windows; sensitive to superficial text length.
* [Source: https://arxiv.org/abs/2604.22750 (Accessed: 2026-08-08)]

#### 3. Graph Cycle Detection
* **Mechanism:** Models the execution trajectory as a directed multi-graph $G = (V, E)$, where vertices $V$ represent discrete agent states or tool invocation signatures, and directed edges $E$ represent state transitions. Runs Tarjan's strongly connected components (SCC) algorithm or Depth-First Search (DFS) back-edge detection on the active trajectory graph.
* **Pros:** Ideal for multi-agent delegation topologies and tool ping-pong ($A \rightarrow B \rightarrow C \rightarrow A$). Detects complex multi-step cyclic paths regardless of step content.
* **Cons:** Requires explicit state hashing or node classification to map continuous LLM outputs into discrete graph vertices.
* [Source: https://docs.langchain.com/oss/python/langgraph/persistence (Accessed: 2026-08-08)]

#### 4. Semantic Embedding Similarity vs. Exact Hash Matching

| Feature / Metric | Exact Hash Matching | Semantic Embedding Similarity |
|---|---|---|
| **Underlying Mechanism** | Canonical JSON SHA-256 Digest | Vector Cosine Similarity ($v_1 \cdot v_2 / \|v_1\|\|v_2\|$) |
| **Execution Latency** | $<0.1\text{ ms}$ (In-memory CPU) | $10 - 50\text{ ms}$ (Requires vector embedding model call) |
| **Robustness to Variation** | Zero tolerance (exact byte equality) | High (catches paraphrased queries, synonym swaps, re-ordered keys) |
| **False Positive Rate** | 0.0% | Low-to-Moderate (requires calibrated cosine threshold, e.g., $\ge 0.92$) |
| **Primary Use Case** | Fast-path filter for identical tool arguments | Deep trajectory inspection for semantic thought/prompt loops |

* [Source: https://runguard.dev/blog/autogen-cost-control-loop-detection.html (Accessed: 2026-08-08)]
* [Source: https://iternal.ai/token-usage-guide (Accessed: 2026-08-08)]

---

## 2. Token & Cost Budget Enforcement

### 2.1 Tracking Token Dimensions in 2026
Modern LLM provider billing architectures (OpenAI, Anthropic, Google) split usage across four primary token categories:

1. **Input Tokens (Prompt Tokens):** Raw context tokens passed into the model, including system instructions, message history, and tool definitions.
2. **Output Tokens (Completion Tokens):** Visible text and structured tool call arguments generated by the model.
3. **Reasoning Tokens:** Internal chain-of-thought tokens generated by reasoning models (e.g., OpenAI o1/o3, DeepSeek-R1). Billed at standard output token rates, returned under `output_tokens_details.reasoning_tokens`.
4. **Cached Input Tokens:** Discounted prompt tokens served from provider key-value caches (e.g., Anthropic Prompt Caching, OpenAI Cached Input), providing up to 90% cost reduction on static system prompts and long context histories.

* [Source: https://developers.openai.com/api/docs/guides/reasoning (Accessed: 2026-08-08)]
* [Source: https://community.openai.com/t/openai-agent-sdk-token-usage-and-reasoning-output/1316366 (Accessed: 2026-08-08)]
* [Source: https://iternal.ai/token-usage-guide (Accessed: 2026-08-08)]

---

### 2.2 Budget Limits & Enforcement Mechanisms

#### Hard vs. Soft Budget Limits
* **Soft Limits (e.g., 80% of budget ceiling):**
  * **Triggers:** Emits `BudgetWarning` events, logs telemetry metrics, fires webhooks, and enables dynamic model downgrading (e.g., routing remaining sub-tasks from Claude 3.5 Sonnet / GPT-4o to Claude 3.5 Haiku / GPT-4o-mini).
  * **Purpose:** Proactively curbs spend while allowing high-priority tasks to complete without abrupt termination.
* **Hard Limits (e.g., 100% of budget ceiling):**
  * **Triggers:** Synchronously halts model execution, raises `BudgetExceededError`, and invokes `chowki` warm-resume snapshot preservation.
  * **Purpose:** Absolute guardrail against unexpected financial exposure or runaway loops.

#### Alert vs. Auto-Pause Mechanisms
* **Alert Mechanism:** Asynchronous, non-blocking notification dispatched via webhooks or event buses to monitoring systems (Slack, Datadog, Prometheus) while execution continues.
* **Auto-Pause Mechanism:** Synchronously suspends execution, persists the state delta snapshot to `chowki` durable storage, and routes execution state to the `chowki` Human-In-The-Loop (HITL) control plane for manual approval or context compaction.

* [Source: https://runguard.dev/blog/autogen-cost-control-loop-detection.html (Accessed: 2026-08-08)]
* [Source: https://zalt.me/blog/2026/06/ai-guardrails-output-validation (Accessed: 2026-08-08)]

---

## 3. Error-Class Taxonomy & Actions

### 3.1 Standardized Error Classes for Agent Execution

1. **`RateLimitError`**
   * **Trigger:** Provider HTTP 429 (Too Many Requests) or 529 (Overloaded), RPM/TPM quota exhaustion.
   * **Scope:** Transient API infrastructure throttling.
2. **`ContextWindowExceeded`**
   * **Trigger:** Total prompt, context history, and tool definitions exceed model context length limits (e.g., 128k/200k tokens).
   * **Scope:** Prompt structure / memory allocation capacity limit.
3. **`ToolExecutionError`**
   * **Trigger:** External tool execution exception, non-zero return code, tool execution timeout, or invalid return format.
   * **Scope:** External integration / runtime environment failure.
4. **`ValidationFailure` / `HallucinationError`**
   * **Trigger:** Model output violates JSON schema, fails Pydantic validation, or breaches Guardrails rules (e.g., invalid enum choice, PII detection).
   * **Scope:** Output quality / structured formatting failure.
5. **`InfiniteLoopDetected`**
   * **Trigger:** Repeated tool call window threshold hit, Levenshtein edit similarity breached, or graph cycle detected.
   * **Scope:** Trajectory control / cycle prevention rule violation.
6. **`BudgetExceeded`**
   * **Trigger:** Cumulative token limit or monetary cost threshold exceeded per run / step / session.
   * **Scope:** Financial governance policy limit.

---

### 3.2 Standardized Action Mapping Strategy

| Error Class | Primary Action | Secondary / Fallback Action | Execution Mechanics |
|---|---|---|---|
| **`RateLimitError`** | **Auto-Retry** | **Auto-Pause** | Exponential backoff with full jitter ($T_{\text{wait}} = \min(T_{\text{max}}, T_{\text{base}} \times 2^{\text{attempt}} + \text{jitter})$). Max 3 retries before auto-pausing. |
| **`ContextWindowExceeded`** | **Auto-Summarize** | **Abort** | Compact context history via sliding window or summarizer node. If summarization is disabled or fails, escalate to Abort. |
| **`ToolExecutionError`** | **Auto-Retry** | **Auto-Pause** | Retry transient network/API failures up to 2 times. Pass deterministic tool errors back into LLM context or trigger Auto-Pause for HITL review. |
| **`ValidationFailure`** | **Auto-Reask** | **Auto-Fix / Auto-Pause** | Re-prompt LLM with structured validation failure message (max 2 reasks). If reasks fail, apply deterministic fix or auto-pause. |
| **`InfiniteLoopDetected`** | **Auto-Pause** | **Abort** | Synchronously suspend execution, persist warm state snapshot via `chowki`, and request human intervention via HITL control plane. |
| **`BudgetExceeded`** | **Auto-Pause** | **Abort** | Soft limit: trigger alert + model downgrade. Hard limit: immediately pause, snapshot state in `chowki`, or abort if HITL disabled. |

* [Source: https://guardrailsai.com/guardrails/docs/concepts/validator_on_fail_actions (Accessed: 2026-08-08)]
* [Source: https://www.agentnotebook.dev/tutorials/langgraph-recursion-limit (Accessed: 2026-08-08)]
* [Source: https://zalt.me/blog/2026/06/ai-guardrails-output-validation (Accessed: 2026-08-08)]

---

## 4. Prior Art & Defaults

### 4.1 Comparative Evaluation of Framework Guardrails

#### 1. LangChain / LangGraph
* **Mechanism:** Enforces `recursion_limit` (default: 25 supersteps) tracking total node visits across state graph executions. Raises `GraphRecursionError` when exceeded.
* **Graceful Handling:** Exposes `RemainingSteps` managed value to enable nodes to perform fallback actions or graceful exits before hitting hard bounds.
* **Gaps:** Does not natively detect semantic tool ping-pong or track monetary budget limits in core open source.
* [Source: https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT (Accessed: 2026-08-08)]
* [Source: https://markaicode.com/errors/langgraph-recursion-limit-reached-fix/ (Accessed: 2026-08-08)]

#### 2. AutoGen (Microsoft)
* **Mechanism:** Provides `max_consecutive_auto_reply` (per-agent counter), `max_turns`, and `is_termination_msg` (e.g., checking for `"TERMINATE"` string in messages).
* **Gaps:** Critical architectural flaw identified in 2026 production analysis: `max_consecutive_auto_reply` tracks single-agent consecutive turns. In multi-agent GroupChats where speaker selection alternates ($A \rightarrow B \rightarrow A$), per-agent counters reset every turn, rendering `max_consecutive_auto_reply` ineffective against multi-agent cost explosions.
* [Source: https://microsoft.github.io/autogen/0.2/docs/tutorial/chat-termination/ (Accessed: 2026-08-08)]
* [Source: https://runguard.dev/blog/autogen-cost-control-loop-detection.html (Accessed: 2026-08-08)]

#### 3. CrewAI
* **Mechanism:** Provides `max_iter` (default: 20 or 25 iterations per task), `max_rpm` (requests per minute rate limit), `max_execution_time` (wall-clock execution ceiling in seconds), and `max_retry_limit` (default: 2).
* **Gaps:** Frequently suffers from repeated tool calling loops when agents are unsatisfied with tool output, relying purely on iteration caps rather than real-time semantic cycle detection.
* [Source: https://docs.crewai.com/en/concepts/agents (Accessed: 2026-08-08)]
* [Source: https://joshuaopolko.com/crewai-setup-production-guide/ (Accessed: 2026-08-08)]

#### 4. Guardrails AI
* **Mechanism:** Wraps LLM calls in `Guard` objects configured with validators and `OnFailAction` policies (`REASK`, `FIX`, `FILTER`, `REFRAIN`, `NOOP`, `EXCEPTION`, `FIX_REASK`).
* **Control:** Reask iterations are bounded by `num_reasks` (default reask limit) to prevent reask loops.
* **Gaps:** Focused exclusively on single-turn I/O and structured schema validation; lacks state-graph persistence, warm resume, or multi-agent trajectory cycle detection.
* [Source: https://guardrailsai.com/guardrails/docs/concepts/validator_on_fail_actions (Accessed: 2026-08-08)]
* [Source: https://qaskills.sh/blog/guardrails-ai-validators-guide-2026 (Accessed: 2026-08-08)]

---

### 4.2 Recommended Sensible Defaults for `chowki`

To provide robust, zero-configuration protection out of the box while maintaining high developer ergonomics, `chowki` should adopt the following production defaults:

| Guardrail Parameter | Recommended Default | Rationale & Mechanism |
|---|---|---|
| `max_steps_per_run` | **25 steps** | Standard baseline across LangGraph (25) and CrewAI (25). Prevents runaway execution while allowing multi-step workflows. |
| `tool_loop_window_size` | **5 steps** | Sliding window size $k=5$ for tracking consecutive tool invocations. |
| `tool_loop_max_repeats` | **3 duplicate calls** | Triggers `InfiniteLoopDetected` when 3 identical tool calls (`tool_name` + canonicalized `kwargs` hash) occur within 5 steps. |
| `semantic_loop_threshold` | **0.85 similarity** | Triggers warning at 0.85 Levenshtein/cosine similarity over 3 consecutive steps; triggers `InfiniteLoopDetected` Auto-Pause at 0.95. |
| `max_auto_retries` | **3 attempts** | Used for `RateLimitError` (HTTP 429/529) and transient `ToolExecutionError` with exponential backoff (base 1.0s, max 30s). |
| `max_validation_reasks` | **2 reasks** | Caps LLM self-correction loops for `ValidationFailure`, balancing output quality with token spend. |
| `soft_budget_threshold` | **80% of budget cap** | Emits `BudgetWarning` event, logs warning metrics, and triggers optional model downgrade rule. |
| `hard_budget_action` | **Auto-Pause (HITL)** | Halts execution immediately at 100% budget cap, persisting full state delta snapshot to `chowki` storage for human review. |

* [Source: https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT (Accessed: 2026-08-08)]
* [Source: https://docs.crewai.com/en/concepts/agents (Accessed: 2026-08-08)]
* [Source: https://runguard.dev/blog/autogen-cost-control-loop-detection.html (Accessed: 2026-08-08)]

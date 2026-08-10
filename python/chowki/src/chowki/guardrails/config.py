"""Guardrail configuration defaults (docs/research/04-guardrails.md:169-183).

Default values from research table:
- max_steps_per_run: 25
- tool_loop_window_size: 5
- tool_loop_max_repeats: 3
- semantic_loop_warn_threshold: 0.85
- semantic_loop_pause_threshold: 0.95
- semantic_loop_consecutive: 3
- max_auto_retries: 3
- max_validation_reasks: 2
- retry_base_seconds: 1.0
- retry_max_seconds: 30.0
- soft_budget_threshold: 0.80
- max_token_budget: None
- max_cost_usd: None
- hard_budget_action: "PAUSE"
- enabled: True
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True, frozen=True)
class GuardrailConfig:
    max_steps_per_run: int = 25
    tool_loop_window_size: int = 5
    tool_loop_max_repeats: int = 3
    semantic_loop_warn_threshold: float = 0.85
    semantic_loop_pause_threshold: float = 0.95
    semantic_loop_consecutive: int = 3
    max_auto_retries: int = 3
    max_validation_reasks: int = 2
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0
    soft_budget_threshold: float = 0.80
    max_token_budget: int | None = None
    max_cost_usd: float | None = None
    hard_budget_action: Literal["PAUSE", "ABORT"] = "PAUSE"
    enabled: bool = True

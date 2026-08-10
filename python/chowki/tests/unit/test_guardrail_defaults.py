from __future__ import annotations

from chowki.guardrails.config import GuardrailConfig


def test_defaults_match_the_researched_table() -> None:
    """docs/research/04-guardrails.md:169-183. Changing a value here is an ADR change."""
    g = GuardrailConfig()
    assert g.max_steps_per_run == 25
    assert g.tool_loop_window_size == 5
    assert g.tool_loop_max_repeats == 3
    assert g.semantic_loop_warn_threshold == 0.85
    assert g.semantic_loop_pause_threshold == 0.95
    assert g.semantic_loop_consecutive == 3
    assert g.max_auto_retries == 3
    assert g.max_validation_reasks == 2
    assert g.retry_base_seconds == 1.0
    assert g.retry_max_seconds == 30.0
    assert g.soft_budget_threshold == 0.80
    assert g.max_token_budget is None
    assert g.max_cost_usd is None
    assert g.hard_budget_action == "PAUSE"
    assert g.enabled is True

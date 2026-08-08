"""Normative hot-path performance budgets for chowki.

Sources:
  docs/research/00-synthesis.md:162-186   (per-step snapshot overhead breakdown)
  docs/research/02-serialization.md:353-379 (component budgets, delta chain depth)

A change to any number here is an architectural decision and requires a plan update,
not a test tweak. Never relax a budget to make a test pass.
"""

from __future__ import annotations

from typing import Final

#: Reference payload size for all *_1mb_* budgets.
REFERENCE_STATE_BYTES: Final = 1_048_576

BUDGETS: Final[dict[str, float]] = {
    # --- Per-step snapshot pipeline, 1 MiB state (total must be < 2.0 ms) ---
    "redaction_1mb_ms": 0.8,
    "encode_1mb_ms": 0.3,
    "canonical_hash_1mb_ms": 0.3,
    "encrypt_1mb_ms": 0.4,
    "dispatch_ms": 0.2,
    "snapshot_total_1mb_ms": 2.0,
    # --- Delta persistence and warm resume ---
    "delta_diff_1mb_ms": 1.0,
    "warm_resume_base_plus_10_deltas_ms": 2.5,
    # --- Decorator and guardrail overhead, per step ---
    "step_decorator_overhead_us": 50.0,
    "loop_detect_step_us": 100.0,
    "budget_track_step_us": 20.0,
}

#: Multiplier applied to every budget before asserting, to absorb local dev box
#: CI runner noise.
TOLERANCE: Final = 1.5


def limit_seconds(name: str) -> float:
    """Return the tolerance-adjusted budget for ``name`` in seconds."""
    raw = BUDGETS[name]
    value_ms = raw / 1000.0 if name.endswith("_us") else raw
    return value_ms * TOLERANCE / 1000.0

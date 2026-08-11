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
    # --- Per-step snapshot pipeline, 1 MiB state (total must be < 3.5 ms) ---
    "redaction_1mb_ms": 0.8,
    # The research figure is 0.3 ms for msgspec's *Struct* encoder. The 1 MiB gate
    # encodes an untyped dict tree through the slower generic path, and the dev-box
    # median for it is bimodal (~0.20 ms or ~0.45 ms depending on where the OS places
    # the process) — see docs/plans/01-foundation.md (git history), Task 8 note. Gate raised
    # to 0.6 ms to sit above the slow mode; snapshot_total_1mb_ms remains the binding
    # 3.5 ms end-to-end claim, so component gates no longer sum to it.
    "encode_1mb_ms": 0.6,
    "canonical_hash_1mb_ms": 0.35,
    "encrypt_1mb_ms": 0.4,
    "dispatch_ms": 0.2,
    # End-to-end total budget for 1 MiB snapshot pipeline.
    # Set to 3.5 ms base (5.25 ms allowed at 1.5 tolerance) to account for per-object
    # container traversal over object-dense state (2,400 dicts) alongside the 1 MB byte scan.
    "snapshot_total_1mb_ms": 3.5,
    # --- Delta persistence and warm resume ---
    "delta_diff_1mb_ms": 1.0,
    "warm_resume_base_plus_10_deltas_ms": 2.5,
    # --- Decorator and guardrail overhead, per step ---
    "step_decorator_overhead_us": 50.0,
    "loop_detect_step_us": 100.0,
    "budget_track_step_us": 20.0,
}

#: Multiplier applied to every budget before asserting, to absorb CI runner noise.
#: Local runs and CI use the same factor so a "green locally, red in CI" split is
#: impossible. Tighten only with a plan update.
TOLERANCE: Final = 1.5


def limit_seconds(name: str) -> float:
    """Return the tolerance-adjusted budget for ``name`` in seconds."""
    raw = BUDGETS[name]
    value_ms = raw / 1000.0 if name.endswith("_us") else raw
    return value_ms * TOLERANCE / 1000.0

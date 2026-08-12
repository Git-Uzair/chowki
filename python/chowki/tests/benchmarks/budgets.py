"""Normative hot-path performance budgets for chowki.

Sources:
  docs/research/00-synthesis.md:171-193   (per-step snapshot overhead breakdown)
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
    # Reference figure, not the enforced gate. Hashing 1 MiB *is* OpenSSL's SHA-256 core:
    # chowki's own contribution (hex encode + the "sha256:" prefix) measures 0.9 us, 0.2%
    # of the total. The absolute number therefore tracks the CPU, not the library — 0.482 ms
    # (2.18 GB/s) on the reference dev box against 0.751 ms (1.40 GB/s) on a shared CI
    # runner, a 1.6x spread no chowki change can move. Enforcement moved to
    # HASH_OVERHEAD_MAX plus canonical_hash_1mb_ceiling_ms; see test_hash_bench.py.
    "canonical_hash_1mb_ms": 0.35,
    # Absolute backstop for the same operation, sized so every CPU chowki supports clears
    # it while a regression that walks the buffer twice (measured ratio 2.0) still fails.
    "canonical_hash_1mb_ceiling_ms": 1.0,
    "encrypt_1mb_ms": 0.4,
    "dispatch_ms": 0.2,
    # End-to-end total budget for 1 MiB snapshot pipeline.
    # Set to 3.5 ms base (5.25 ms allowed at 1.5 tolerance) to account for per-object
    # container traversal over object-dense state (2,400 dicts) alongside the 1 MB byte scan.
    "snapshot_total_1mb_ms": 3.5,
    # --- Delta persistence and warm resume ---
    "delta_diff_1mb_ms": 1.0,
    "warm_resume_base_plus_10_deltas_ms": 2.5,
    # Cold path: reconstruct a run from stored envelopes, as `chowki resume` does. Distinct
    # from the warm gate above, which covers `DeltaChain.materialize()` over trees already
    # in memory (0.064 ms measured) — only 1.5% of this figure. The rest is msgpack decode
    # of the 1 MiB base (0.85 ms), the `inline_blobs` walk (2.08 ms) and the defensive
    # `_copy_containers` walk (1.31 ms). Base set from measurement: 4.33 ms on the reference
    # dev box, 4.95 ms on a shared CI runner. Runs once per resume, not per step.
    "cold_load_base_plus_10_deltas_ms": 5.0,
    # --- Decorator and guardrail overhead, per step ---
    "step_decorator_overhead_us": 50.0,
    "loop_detect_step_us": 100.0,
    "budget_track_step_us": 20.0,
}

#: Multiplier applied to every budget before asserting, to absorb CI runner noise.
#: Local runs and CI use the same factor so a "green locally, red in CI" split is
#: impossible. Tighten only with a plan update.
TOLERANCE: Final = 1.5

#: Ceiling on what chowki may add on top of the platform's own SHA-256 digest, as a ratio
#: measured on the machine running the test. Hardware-independent where an absolute
#: millisecond gate is not: the two medians are sampled alternately on one CPU, so the
#: runner's throughput cancels out. Measured ratio is 1.000 +/- 0.006 across trials and 2.008
#: for a regression that hashes the buffer twice, so 1.15 sits ~15x clear of the noise.
HASH_OVERHEAD_MAX: Final = 1.15


def limit_seconds(name: str) -> float:
    """Return the tolerance-adjusted budget for ``name`` in seconds."""
    raw = BUDGETS[name]
    value_ms = raw / 1000.0 if name.endswith("_us") else raw
    return value_ms * TOLERANCE / 1000.0

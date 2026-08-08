"""Unit tests for scripts/ci_local.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

import scripts.ci_local as ci_local  # noqa: E402


def test_ci_local_steps_contains_sync() -> None:
    """STEPS in ci_local must contain the sync step with --locked flag."""
    expected_step = ("sync", ["uv", "sync", "--locked", "--all-extras", "--dev"])
    assert expected_step in ci_local.STEPS

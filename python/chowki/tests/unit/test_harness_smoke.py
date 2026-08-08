"""Smoke test proving the chowki test harness is wired end to end."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st

import chowki


def test_package_imports_and_exposes_version() -> None:
    assert isinstance(chowki.__version__, str)
    assert chowki.__version__


def test_package_is_typed() -> None:
    """PEP 561 marker must ship so downstream pyright/mypy see chowki's types."""
    from importlib.resources import files

    assert (files("chowki") / "py.typed").is_file()


@pytest.mark.asyncio
async def test_async_harness_runs() -> None:
    await asyncio.sleep(0)
    assert True


@given(st.integers(min_value=0, max_value=1_000))
def test_hypothesis_harness_runs(value: int) -> None:
    assert value >= 0


def test_assert_budget_raises_when_stats_missing() -> None:
    import importlib.util
    from pathlib import Path

    conftest_path = Path(__file__).parents[1] / "benchmarks" / "conftest.py"
    spec = importlib.util.spec_from_file_location("benchmark_conftest", conftest_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert_fn = mod.assert_budget.__wrapped__()

    class DummyNoStats:
        disabled = False
        stats = None

    with pytest.raises(RuntimeError, match=r"benchmark\.stats is missing"):
        assert_fn(DummyNoStats(), "canonical_hash_1mb_ms")

    class DummyNoInnerStats:
        disabled = False
        stats = type("Stats", (), {"stats": None})()

    with pytest.raises(RuntimeError, match=r"benchmark\.stats\.stats is missing"):
        assert_fn(DummyNoInnerStats(), "canonical_hash_1mb_ms")

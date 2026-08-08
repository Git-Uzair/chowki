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

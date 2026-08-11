"""Shared test fixtures for chowki test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chowki.config import ChowkiConfig, ChowkiEngine, reset_engine
from chowki.core.registry import clear_registry
from chowki.storage.memory import MemoryStorage


@pytest.fixture(autouse=True)
def autoclear_registry() -> Iterator[None]:
    clear_registry()
    try:
        yield
    finally:
        clear_registry()


@pytest.fixture
def engine() -> Iterator[ChowkiEngine]:
    eng = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))
    try:
        yield eng
    finally:
        eng.close()
        reset_engine()

"""Shared test fixtures for chowki test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from chowki.config import ChowkiConfig, ChowkiEngine, reset_engine
from chowki.storage.memory import MemoryStorage


@pytest.fixture
def engine() -> Iterator[ChowkiEngine]:
    eng = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))
    try:
        yield eng
    finally:
        eng.close()
        reset_engine()

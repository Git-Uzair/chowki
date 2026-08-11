"""Shared test fixtures for chowki test suite."""

from __future__ import annotations

import os
import shlex
from collections.abc import Callable, Iterator

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
def split_command() -> Callable[[str], list[str]]:
    """Tokenise a printed shell command back into argv the way the local shell would.

    `shlex.split` in POSIX mode eats the backslashes of Windows paths, so Windows is
    tokenised in non-POSIX mode and the quotes that mode keeps are stripped off.
    """

    def split(command: str) -> list[str]:
        if os.name == "nt":
            return [tok.strip('"') for tok in shlex.split(command, posix=False)]
        return shlex.split(command)

    return split


@pytest.fixture
def engine() -> Iterator[ChowkiEngine]:
    eng = ChowkiEngine(ChowkiConfig(storage=MemoryStorage()))
    try:
        yield eng
    finally:
        eng.close()
        reset_engine()

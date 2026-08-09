from __future__ import annotations

from pathlib import Path

from chowki.storage.base import StorageAdapter
from chowki.storage.memory import MemoryStorage
from chowki.storage.sqlite import SQLiteStorage

DEFAULT_DB_PATH = Path(".chowki") / "chowki.db"

__all__ = [
    "DEFAULT_DB_PATH",
    "MemoryStorage",
    "SQLiteStorage",
    "StorageAdapter",
]

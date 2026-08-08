# scripts/check_layout.py
"""Structural guard for the chowki monorepo layout (ADR-001)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_DIRS = [
    ".github/workflows",
    "docs/plans",
    "docs/research",
    "examples/node",
    "examples/python",
    "node",
    "python",
    "spec/scripts",
    "spec/v1",
]

REQUIRED_FILES = [
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "LICENSE",
    "README.md",
    "node/README.md",
    "spec/README.md",
]

BANNED_WORD = "check" + "point"  # split so this guard never trips on itself


def main() -> int:
    failures: list[str] = []
    for rel in REQUIRED_DIRS:
        if not (ROOT / rel).is_dir():
            failures.append(f"missing directory: {rel}")
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            failures.append(f"missing file: {rel}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".venv" in path.parts:
            continue
        if path.suffix not in {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".cfg"}:
            continue
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if BANNED_WORD in text.lower():
            failures.append(f"banned product term in {path.relative_to(ROOT)}")
        if text and not text.endswith("\n"):
            failures.append(f"missing trailing newline: {path.relative_to(ROOT)}")

    for line in failures:
        print(f"FAIL: {line}", file=sys.stderr)
    print("layout OK" if not failures else f"{len(failures)} layout failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

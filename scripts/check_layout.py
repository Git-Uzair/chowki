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

EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    ".chowki",
    ".benchmarks",
    ".hypothesis",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
}

CHECKED_SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".cfg",
    ".txt",
    ".ts",
    ".js",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
    ".html",
    ".css",
    ".scss",
    ".rs",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".rst",
    ".ini",
    ".lock",
}

BANNED_WORD = "check" + "point"  # split so this guard never trips on itself


def main() -> int:
    failures: list[str] = []
    for rel in REQUIRED_DIRS:
        if not (ROOT / rel).is_dir():
            failures.append(f"missing directory: {rel}")
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            failures.append(f"missing file: {rel}")

    script_path = Path(__file__).resolve()

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue

        rel_path = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIR_NAMES or part.endswith(".egg-info") for part in rel_path.parts):
            continue

        if path.resolve() == script_path:
            continue

        if not (path.suffix in CHECKED_SUFFIXES or path.suffix == "" or path.name.startswith(".")):
            continue

        text = path.read_text(encoding="utf-8", errors="ignore")
        if BANNED_WORD in text.lower():
            failures.append(f"banned product term in {rel_path}")
        if not text.endswith("\n"):
            failures.append(f"missing trailing newline: {rel_path}")
        elif text.endswith("\n\n"):
            failures.append(f"multiple trailing newlines: {rel_path}")

    for line in failures:
        print(f"FAIL: {line}", file=sys.stderr)
    print("layout OK" if not failures else f"{len(failures)} layout failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

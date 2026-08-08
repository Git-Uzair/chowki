"""Unit tests for scripts/check_layout.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

import scripts.check_layout as check_layout  # noqa: E402


def test_check_layout_passes_on_repo() -> None:
    """The repository itself must pass layout check."""
    assert check_layout.main() == 0


def test_check_layout_detects_multiple_trailing_newlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files ending in \\n\\n must fail."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_text("content\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("content\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("content\n", encoding="utf-8")
    (tmp_path / ".gitattributes").write_text("content\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("content\n", encoding="utf-8")

    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    bad_file = tmp_path / "test.ts"
    bad_file.write_text("console.log('hi');\n\n", encoding="utf-8")

    assert check_layout.main() == 1


@pytest.mark.parametrize(
    "filename",
    [
        "sample.ts",
        "sample.js",
        "sample.pyi",
        "sample.tsx",
        "sample.jsx",
        "sample.mdx",
        "sample.txt",
        "LICENSE",
        "README",
        ".gitignore",
        ".gitattributes",
        ".gitkeep",
    ],
)
def test_check_layout_detects_banned_term_in_various_file_types(
    filename: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Banned product term in .ts, .js, .pyi, .tsx, .jsx, .mdx and extensionless files must fail."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"
    (tmp_path / filename).write_text(f"some {banned} here\n", encoding="utf-8")

    assert check_layout.main() == 1


def test_check_layout_ignores_coverage_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.coverage files must be ignored even if containing banned terms or bad newlines."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"
    (tmp_path / ".coverage").write_text(f"binary {banned}\n\n", encoding="utf-8")
    (tmp_path / ".coverage.machine.123.456").write_text(f"binary {banned}\n\n", encoding="utf-8")

    assert check_layout.main() == 0


def test_check_layout_ignores_excluded_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Build, cache, vendor, and venv directories must be ignored."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"
    for excluded in (
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
        "my_pkg.egg-info",
    ):
        ex_dir = tmp_path / excluded
        ex_dir.mkdir(parents=True, exist_ok=True)
        (ex_dir / "bad.py").write_text(f"bad = '{banned}'\n\n", encoding="utf-8")

    assert check_layout.main() == 0

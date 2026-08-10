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
        "py.typed",
        "config.xml",
        "data.jsonl",
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
    """Banned product term in .ts, .js, .pyi, py.typed, .xml, .jsonl, etc. must fail."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"
    (tmp_path / filename).write_text(f"some {banned} here\n", encoding="utf-8")

    assert check_layout.main() == 1


def test_check_layout_detects_banned_term_in_path_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path containing banned product term in filename or directory name must fail."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"

    # Test file with banned term in filename
    bad_file = tmp_path / f"my_{banned}_module.py"
    bad_file.write_text("valid_content = 123\n", encoding="utf-8")

    assert check_layout.main() == 1


def test_check_layout_detects_banned_term_in_dir_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Directory name containing banned product term must fail."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"

    bad_dir = tmp_path / f"sub_{banned}_dir"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "valid.py").write_text("valid = True\n", encoding="utf-8")

    assert check_layout.main() == 1


def test_check_layout_scans_files_named_dist_or_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain files named 'dist' or 'build' are scanned and not skipped as excluded directories."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    banned = "check" + "point"

    # A plain file named dist containing a banned term must fail
    dist_file = tmp_path / "dist"
    dist_file.write_text(f"banned = '{banned}'\n", encoding="utf-8")

    assert check_layout.main() == 1


def test_check_layout_detects_a_top_level_source_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A language source tree at the repo root violates ADR-001 and must fail."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("content\n", encoding="utf-8")

    assert check_layout.main() == 0

    stray = tmp_path / "src" / "chowki" / "core"
    stray.mkdir(parents=True, exist_ok=True)
    (stray / "decorators.py").write_text("stray = True\n", encoding="utf-8")

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
        ".worktrees",
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


def test_check_layout_detects_bare_cr_trailing_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files ending in bare \\r without \\n must fail as missing trailing newline."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"content\n")

    bad_file = tmp_path / "test.txt"
    bad_file.write_bytes(b"content\r")

    assert check_layout.main() == 1


def test_check_layout_scans_script_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scripts/check_layout.py is required and scanned during layout checks."""
    assert "scripts/check_layout.py" in check_layout.REQUIRED_FILES

    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"content\n")

    banned = "check" + "point"
    script_file = tmp_path / "scripts" / "check_layout.py"
    script_file.write_bytes(f"banned = '{banned}'\n".encode())

    assert check_layout.main() == 1


def test_check_layout_supports_utf16_text_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UTF-16 text files with NUL bytes are handled and checked for banned terms and newlines."""
    monkeypatch.setattr(check_layout, "ROOT", tmp_path)
    for d in check_layout.REQUIRED_DIRS:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    for f in check_layout.REQUIRED_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"content\n")

    utf16_file = tmp_path / "powershell_output.txt"
    utf16_file.write_bytes("clean content\n".encode("utf-16le"))

    assert check_layout.main() == 0

    banned = "check" + "point"
    utf16_file.write_bytes(f"banned {banned} here\n".encode("utf-16le"))
    assert check_layout.main() == 1

    utf16_file.write_bytes("no trailing newline".encode("utf-16le"))
    assert check_layout.main() == 1

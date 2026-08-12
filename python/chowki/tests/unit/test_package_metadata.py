"""Tests for package metadata, entry points, and shipped typing markers."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import shutil
import subprocess
import tarfile
import zipfile
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any, cast

import pytest

import chowki


def test_py_typed_marker_ships_in_package() -> None:
    """Verify that the py.typed marker file exists in the installed chowki package."""
    ref = importlib.resources.files("chowki") / "py.typed"
    assert ref.is_file()


def test_entry_point_resolves() -> None:
    """Verify that the console_scripts entry point 'chowki = chowki.cli:main' resolves cleanly."""
    eps = importlib.metadata.entry_points()
    if hasattr(eps, "select"):
        console_scripts = list(eps.select(group="console_scripts"))
    else:
        console_scripts = list(cast(Any, eps).get("console_scripts", []))

    chowki_ep = next((ep for ep in console_scripts if ep.name == "chowki"), None)
    assert chowki_ep is not None, "Console script 'chowki' entry point not found"
    assert chowki_ep.value == "chowki.cli:main"
    main_fn = chowki_ep.load()
    assert callable(main_fn)


def test_version_not_fallback_when_tag_present() -> None:
    """Verify package version matches tag when tag is present on current commit."""
    git_bin = shutil.which("git")
    if not git_bin:
        pytest.skip("Git binary not found")

    try:
        res = subprocess.run(  # noqa: S603
            [git_bin, "describe", "--tags", "--exact-match"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        pytest.skip("Git command unavailable")

    if res.returncode != 0 or not res.stdout.strip():
        pytest.skip("No git tag on current commit")

    tag = res.stdout.strip().lstrip("v")
    version = chowki.__version__
    assert version == tag, f"Version {version} does not match git tag {tag}"


def test_package_classifiers_and_metadata() -> None:
    """Verify package metadata contains required classifiers and URLs."""
    try:
        meta = importlib.metadata.metadata("chowki")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("chowki package metadata not found in current environment")

    assert meta["Name"] == "chowki"
    classifiers = meta.get_all("Classifier") or []
    assert "Framework :: AsyncIO" in classifiers
    assert "Operating System :: OS Independent" in classifiers
    assert "Typing :: Typed" in classifiers
    assert "License :: OSI Approved :: MIT License" in classifiers


def test_license_file_in_sdist_and_wheel() -> None:
    """Verify LICENSE ships at the sdist root and only under the wheel's dist-info/licenses/."""
    root_dir = Path(__file__).resolve().parents[4]
    dist_dir = root_dir / "dist"

    build_cmd = ["uv", "build", "--package", "chowki"]
    res = subprocess.run(build_cmd, cwd=root_dir, capture_output=True, text=True, check=False)  # noqa: S603
    assert res.returncode == 0, f"uv build failed: {res.stderr}"

    sdists = list(dist_dir.glob("*.tar.gz"))
    wheels = list(dist_dir.glob("*.whl"))
    assert sdists, "No sdist found in dist/"
    assert wheels, "No wheel found in dist/"

    sdist_path = max(sdists, key=lambda p: p.stat().st_mtime)
    wheel_path = max(wheels, key=lambda p: p.stat().st_mtime)

    with tarfile.open(sdist_path) as tar:
        sdist_names = tar.getnames()
        assert any(
            PurePosixPath(name).name == "LICENSE" and len(PurePosixPath(name).parts) == 2
            for name in sdist_names
        ), f"LICENSE not found at the root of sdist {sdist_path.name}: {sdist_names}"

    with zipfile.ZipFile(wheel_path) as zf:
        wheel_names = zf.namelist()

    licensed = [name for name in wheel_names if fnmatch(name, "*.dist-info/licenses/LICENSE")]
    assert licensed, f"LICENSE not found under dist-info/licenses/ in wheel {wheel_path.name}"

    stray = [name for name in wheel_names if "/" not in name.rstrip("/")]
    assert not stray, (
        f"Wheel {wheel_path.name} installs top-level files into site-packages root: {stray}"
    )


def test_package_carries_ai_and_recovery_classifiers() -> None:
    """PyPI classifiers are faceted-search links; without these the package has no AI signal."""
    try:
        meta = importlib.metadata.metadata("chowki")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("chowki package metadata not found in current environment")

    classifiers = meta.get_all("Classifier") or []
    for required in (
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: System :: Recovery Tools",
        "Topic :: System :: Distributed Computing",
        "Topic :: Security :: Cryptography",
        "Topic :: System :: Monitoring",
        "Topic :: Database",
        "Intended Audience :: System Administrators",
    ):
        assert required in classifiers, f"missing classifier: {required}"


def test_package_summary_and_keywords_are_searchable() -> None:
    """`description` is the highest-weighted free text on a PyPI project page."""
    try:
        meta = importlib.metadata.metadata("chowki")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("chowki package metadata not found in current environment")

    summary = meta["Summary"].lower()
    for term in ("durable execution", "llm agents", "memoization", "sqlite"):
        assert term in summary, f"PyPI summary no longer mentions {term!r}"

    keywords = {k.strip() for k in (meta["Keywords"] or "").split(",")}
    for term in ("durable-execution", "crash-recovery", "memoization", "human-in-the-loop"):
        assert term in keywords, f"PyPI keywords no longer contain {term!r}"

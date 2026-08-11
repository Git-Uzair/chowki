"""Tests for package metadata, entry points, and shipped typing markers."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import shutil
import subprocess
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

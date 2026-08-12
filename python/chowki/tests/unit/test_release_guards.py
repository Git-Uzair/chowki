"""Guards on the release path.

Two classes of bug are covered, both of which are expensive because PyPI never lets a
version number be reused:

1. The build stops deriving its version from the git tag. This actually happened —
   hatch-vcs resolves from the package root (`python/chowki`) while `.git` is two levels
   up, so setuptools-scm never found the repository and every build silently took
   `fallback-version`. A `v0.2.0` tag would have published `0.1.0` a second time.
2. The release workflow loses a gate — the environment binding, the full-history
   checkout, the test run, or the pre-publish guard itself.

These are static and cheap; the end-to-end behaviour is exercised by
`scripts/check_release.py` in the workflow.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_PYPROJECT = REPO_ROOT / "python" / "chowki" / "pyproject.toml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_release import ReleaseError, check, find_artifacts, parse_tag  # noqa: E402


def _package_config() -> dict[str, Any]:
    return tomllib.loads(PACKAGE_PYPROJECT.read_text(encoding="utf-8"))


def _workflow_text() -> str:
    return RELEASE_WORKFLOW.read_text(encoding="utf-8")


# --- 1. the version must come from the tag ---------------------------------------------


def test_hatch_vcs_is_pointed_at_the_repository_root() -> None:
    """Without this override every build silently falls back to a hardcoded version.

    hatch-vcs asks setuptools-scm to resolve from the project directory, which is
    `python/chowki`. `.git` lives two levels above that, and setuptools-scm does not
    search upward, so it raises and hatch-vcs quietly substitutes `fallback-version`.
    The symptom is invisible until the second release, when PyPI rejects the duplicate.
    """
    version_cfg = _package_config()["tool"]["hatch"]["version"]
    assert version_cfg["source"] == "vcs"
    raw_options = version_cfg.get("raw-options")
    assert raw_options is not None, (
        "[tool.hatch.version] has no raw-options; setuptools-scm cannot find .git from "
        "python/chowki and every build will fall back to a hardcoded version"
    )
    assert raw_options.get("root") == "../..", (
        f"raw-options.root is {raw_options.get('root')!r}, expected '../..' so "
        f"setuptools-scm resolves the monorepo root rather than the package directory"
    )


def test_setuptools_scm_actually_resolves_from_the_package_directory() -> None:
    """The functional half of the check above: the override has to really work.

    Asserting the config value alone would still pass if a dependency changed how the
    root is interpreted, so this resolves a version the way the build does.
    """
    pytest.importorskip("setuptools_scm")
    package_dir = PACKAGE_PYPROJECT.parent
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from setuptools_scm import get_version; print(get_version(root='../..', "
            "relative_to=None))",
        ],
        cwd=package_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"setuptools-scm could not read the repo here: {result.stderr.strip()[:120]}")
    resolved = result.stdout.strip()
    fallback = _package_config()["tool"]["hatch"]["version"]["fallback-version"]
    assert resolved, "setuptools-scm returned an empty version"
    assert resolved != fallback, (
        f"setuptools-scm resolved exactly the fallback version ({fallback!r}), which is how "
        f"the original bug looked; the tag is probably not being read"
    )


# --- 2. the workflow must keep its gates -----------------------------------------------


def test_release_workflow_triggers_only_on_version_tags() -> None:
    text = _workflow_text()
    assert "tags:" in text and '"v*"' in text


def test_release_workflow_binds_the_pypi_environment() -> None:
    """The environment name must match the PyPI Trusted Publisher, or OIDC is rejected.

    It is also where a deployment protection rule attaches, which is the only thing
    between an accidental tag push and a permanent version number.
    """
    text = _workflow_text()
    assert "environment:" in text, "release job declares no environment"
    assert "name: pypi" in text, "release job is not bound to the 'pypi' environment"


def test_release_workflow_requests_an_oidc_token() -> None:
    assert "id-token: write" in _workflow_text()


def test_release_workflow_checks_out_full_history() -> None:
    """A shallow checkout has no tags, so the version silently falls back."""
    assert "fetch-depth: 0" in _workflow_text()


def test_release_workflow_runs_the_guard_before_publishing() -> None:
    text = _workflow_text()
    guard = text.find("check_release.py")
    publish = text.find("pypa/gh-action-pypi-publish")
    assert guard != -1, "release workflow never runs scripts/check_release.py"
    assert publish != -1, "release workflow has no publish step"
    assert guard < publish, "the release guard must run before the publish step"


def test_release_workflow_runs_tests_before_publishing() -> None:
    text = _workflow_text()
    tests = text.find("pytest")
    publish = text.find("pypa/gh-action-pypi-publish")
    assert tests != -1, "release workflow publishes without running the test suite"
    assert tests < publish, "tests must run before the publish step"


# --- 3. the guard itself ----------------------------------------------------------------


def _fake_wheel(path: Path, *, name: str, version: str, dunder: str | None = None) -> Path:
    wheel = path / name
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(
            f"chowki-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: chowki\nVersion: {version}\n",
        )
        if dunder is not None:
            zf.writestr("chowki/_version.py", f"__version__ = version = '{dunder}'\n")
    return wheel


def _dist(tmp_path: Path, version: str, *, dunder: str | None = None) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    _fake_wheel(dist, name=f"chowki-{version}-py3-none-any.whl", version=version, dunder=dunder)
    (dist / f"chowki-{version}.tar.gz").write_bytes(b"")
    return dist


@pytest.mark.parametrize("tag", ["0.1.0", "release-1", "v1.2", "vX.Y.Z", "v0.1.0.dev1"])
def test_guard_rejects_malformed_tags(tag: str) -> None:
    with pytest.raises(ReleaseError):
        parse_tag(tag)


@pytest.mark.parametrize("tag", ["v0.1.0", "v1.0.0", "v0.2.0rc1", "v2.0.0b3"])
def test_guard_accepts_release_and_prerelease_tags(tag: str) -> None:
    assert parse_tag(tag) == tag[1:]


def test_guard_rejects_a_version_that_does_not_match_the_tag(tmp_path: Path) -> None:
    """The exact shape of the hatch-vcs bug: tag says 0.2.0, build produced 0.1.0."""
    dist = _dist(tmp_path, "0.1.0")
    with pytest.raises(ReleaseError, match="version mismatch"):
        check("v0.2.0", dist)


def test_guard_rejects_a_dev_version(tmp_path: Path) -> None:
    dist = _dist(tmp_path, "0.1.1.dev176+g7a3637c3")
    with pytest.raises(ReleaseError):
        check("v0.1.1", dist)


def test_guard_rejects_a_dunder_version_that_disagrees(tmp_path: Path) -> None:
    dist = _dist(tmp_path, "0.1.0", dunder="0.0.9")
    with pytest.raises(ReleaseError, match="__version__"):
        check("v0.1.0", dist)


def test_guard_rejects_stale_artifacts_in_dist(tmp_path: Path) -> None:
    """A leftover wheel from a previous build would be uploaded alongside the new one."""
    dist = _dist(tmp_path, "0.1.0")
    _fake_wheel(dist, name="chowki-0.0.9-py3-none-any.whl", version="0.0.9")
    with pytest.raises(ReleaseError, match="exactly one wheel"):
        find_artifacts(dist)


def test_guard_rejects_an_empty_dist(tmp_path: Path) -> None:
    empty = tmp_path / "dist"
    empty.mkdir()
    with pytest.raises(ReleaseError):
        find_artifacts(empty)


@pytest.fixture(name="_offline_pypi")
def offline_pypi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the already-published set so these tests do not depend on live PyPI.

    Without this, `test_guard_accepts_a_correct_release` starts failing the moment its
    hardcoded version is actually released — which is exactly what happened once 0.1.0
    went out. The guard was right; the test was reading the network.
    """
    import check_release

    monkeypatch.setattr(check_release, "_released_versions", lambda: {"0.0.1", "0.1.0"})


@pytest.mark.usefixtures("_offline_pypi")
def test_guard_accepts_a_correct_release(tmp_path: Path) -> None:
    dist = _dist(tmp_path, "0.2.0", dunder="0.2.0")
    notes = check("v0.2.0", dist)
    assert any("METADATA version matches" in n for n in notes)
    assert any("__version__ matches" in n for n in notes)
    assert any("not yet on PyPI" in n for n in notes)


@pytest.mark.usefixtures("_offline_pypi")
def test_guard_rejects_a_version_already_on_pypi(tmp_path: Path) -> None:
    """PyPI never allows a version number to be reused, even after deletion."""
    dist = _dist(tmp_path, "0.1.0", dunder="0.1.0")
    with pytest.raises(ReleaseError, match="already on PyPI"):
        check("v0.1.0", dist)

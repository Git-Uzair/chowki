# scripts/check_release.py
"""Pre-publish guard: refuse to upload anything that does not match the tag.

Runs in the release workflow after `uv build` and before `pypa/gh-action-pypi-publish`.
Every check here failed at least once in development or guards a failure that PyPI makes
permanent, because a version number can never be reused once it is uploaded.

    uv run python scripts/check_release.py --tag v0.1.0 --dist dist

Checks, in order:

1. The tag looks like `v<PEP 440 version>`.
2. `dist/` holds exactly one wheel and one sdist, and nothing else.
3. Both artifacts carry the same version, read from the wheel's own METADATA rather than
   its filename.
4. That version equals the tag with the leading `v` removed. This is the check that
   catches the hatch-vcs misconfiguration that silently built `0.1.0` from every tag.
5. The version is not a `.dev` build and carries no local segment (`+g<sha>`), either of
   which means the build did not happen on a clean checkout of the tag.
6. `chowki.__version__`, baked into the wheel by the vcs build hook, agrees.
7. The version is not already on PyPI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import cast

PYPI_JSON = "https://pypi.org/pypi/chowki/json"

#: PEP 440, restricted to what this project intends to publish: a final release or an
#: explicit pre-release. Deliberately excludes `.dev` and local versions.
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")


class ReleaseError(Exception):
    """A condition that must stop the release."""


def _fail(message: str) -> None:
    raise ReleaseError(message)


def parse_tag(tag: str) -> str:
    """Return the version a tag claims, or fail."""
    if not tag.startswith("v"):
        _fail(f"tag {tag!r} does not start with 'v'; the release workflow triggers on v*")
    version = tag[1:]
    if not _VERSION_RE.match(version):
        _fail(
            f"tag {tag!r} yields version {version!r}, which is not a final release or a "
            f"pre-release (expected e.g. v0.1.0, v0.2.0rc1)"
        )
    return version


def find_artifacts(dist: Path) -> tuple[Path, Path]:
    """Return (wheel, sdist) from dist/, failing on anything unexpected."""
    if not dist.is_dir():
        _fail(f"no dist directory at {dist}")
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    # `uv build` drops a `dist/.gitignore`; dotfiles are tooling, not artifacts. Anything
    # else that is not a wheel or an sdist is unexpected and worth stopping for, because
    # `packages-dir: dist/` uploads the whole directory.
    artifacts = sorted(p.name for p in dist.iterdir() if p.is_file() and not p.name.startswith("."))
    stray = [n for n in artifacts if not n.endswith((".whl", ".tar.gz"))]
    if len(wheels) != 1 or len(sdists) != 1 or stray:
        _fail(
            f"expected exactly one wheel and one sdist in {dist}, found: {artifacts}. "
            f"A stale artifact from an earlier build would be published alongside the new one; "
            f"build into a clean directory."
        )
    return wheels[0], sdists[0]


def _read_metadata_version(wheel: Path) -> str:
    with zipfile.ZipFile(wheel) as zf:
        names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            _fail(f"{wheel.name} does not contain exactly one METADATA file: {names}")
        text = zf.read(names[0]).decode("utf-8")
    for line in text.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    _fail(f"{wheel.name} METADATA has no Version field")
    raise AssertionError("unreachable")


def _read_dunder_version(wheel: Path) -> str | None:
    """Return `chowki.__version__` as baked into the wheel by the vcs build hook."""
    with zipfile.ZipFile(wheel) as zf:
        if "chowki/_version.py" not in zf.namelist():
            return None
        source = zf.read("chowki/_version.py").decode("utf-8")
    match = re.search(r"""__version__\s*=\s*version\s*=\s*['"]([^'"]+)['"]""", source)
    return match.group(1) if match else None


def _released_versions() -> set[str] | None:
    """Versions already on PyPI, or None if PyPI could not be reached."""
    try:
        with urllib.request.urlopen(PYPI_JSON, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return set()  # project does not exist yet: the first release
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return set()
    releases: object = cast("dict[str, object]", payload).get("releases")
    if not isinstance(releases, dict):
        return set()
    return {str(key) for key in cast("dict[object, object]", releases)}


def check(tag: str, dist: Path) -> list[str]:
    """Run every check, returning the lines to print on success."""
    notes: list[str] = []
    version = parse_tag(tag)
    notes.append(f"tag {tag} -> version {version}")

    wheel, sdist = find_artifacts(dist)
    notes.append(f"artifacts: {wheel.name}, {sdist.name}")

    meta_version = _read_metadata_version(wheel)
    if meta_version != version:
        _fail(
            f"version mismatch: tag {tag} claims {version!r} but the wheel METADATA says "
            f"{meta_version!r}. The build did not derive its version from the tag — check "
            f"[tool.hatch.version] raw-options.root in python/chowki/pyproject.toml and "
            f"that the workflow checks out with fetch-depth: 0."
        )
    notes.append(f"wheel METADATA version matches: {meta_version}")

    expected_sdist = f"chowki-{version}.tar.gz"
    if sdist.name != expected_sdist:
        _fail(f"sdist is {sdist.name!r}, expected {expected_sdist!r}")
    notes.append(f"sdist filename matches: {sdist.name}")

    if ".dev" in meta_version or "+" in meta_version:
        _fail(
            f"refusing to publish {meta_version!r}: a .dev or local (+) segment means the "
            f"build was not made from a clean checkout of the tag"
        )
    notes.append("version is a clean release (no .dev, no local segment)")

    dunder = _read_dunder_version(wheel)
    if dunder is None:
        notes.append("chowki/_version.py absent from the wheel — skipped __version__ check")
    elif dunder != version:
        _fail(f"chowki.__version__ is {dunder!r} but the tag claims {version!r}")
    else:
        notes.append(f"chowki.__version__ matches: {dunder}")

    published = _released_versions()
    if published is None:
        notes.append("could not reach PyPI — skipped the already-published check")
    elif version in published:
        _fail(
            f"version {version} is already on PyPI. Version numbers can never be reused, "
            f"even after deletion; tag a new version instead."
        )
    else:
        notes.append(f"version {version} is not yet on PyPI ({len(published)} existing)")

    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-publish release guard for chowki")
    parser.add_argument("--tag", required=True, help="the release tag, e.g. v0.1.0")
    parser.add_argument("--dist", default="dist", type=Path, help="directory holding artifacts")
    args = parser.parse_args()

    try:
        notes = check(str(args.tag), Path(args.dist))
    except ReleaseError as err:
        print(f"RELEASE BLOCKED: {err}", file=sys.stderr)
        return 1

    for note in notes:
        print(f"  ok  {note}")
    print("\nRelease checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

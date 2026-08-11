# scripts/wheel_smoke_test.py
"""Automated wheel smoke test.

Builds sdist and wheel packages into dist/, installs the wheel into a scratch
virtual environment, and verifies 'chowki --version' and the quickstart example.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def get_venv_binaries(venv_dir: Path) -> tuple[Path, Path]:
    """Return paths to python and chowki executables inside a virtual environment."""
    if sys.platform == "win32":
        python_exe = venv_dir / "Scripts" / "python.exe"
        chowki_exe = venv_dir / "Scripts" / "chowki.exe"
    else:
        python_exe = venv_dir / "bin" / "python"
        chowki_exe = venv_dir / "bin" / "chowki"
    return python_exe, chowki_exe


def main() -> int:
    """Run the wheel smoke test sequence."""
    root_dir = Path(__file__).resolve().parent.parent
    dist_dir = root_dir / "dist"

    print("=== Wheel Smoke Test: Building package ===", flush=True)
    build_cmd = ["uv", "build", "--package", "chowki"]
    res = subprocess.run(build_cmd, cwd=root_dir, check=False)  # noqa: S603
    if res.returncode != 0:
        print("FAIL: uv build failed", file=sys.stderr)
        return res.returncode

    wheels = list(dist_dir.glob("*.whl"))
    if not wheels:
        print("FAIL: No .whl file found in dist/", file=sys.stderr)
        return 1

    # Select the newest built wheel
    wheel_path = max(wheels, key=os.path.getmtime)
    print(f"Built wheel: {wheel_path.name}", flush=True)

    with tempfile.TemporaryDirectory(prefix="chowki_smoke_") as tmp_dir:
        scratch_venv = Path(tmp_dir) / "venv"

        print("=== Wheel Smoke Test: Creating scratch venv ===", flush=True)
        venv_cmd = ["uv", "venv", str(scratch_venv)]
        res = subprocess.run(venv_cmd, check=False)  # noqa: S603
        if res.returncode != 0:
            print("FAIL: uv venv creation failed", file=sys.stderr)
            return res.returncode

        python_exe, chowki_exe = get_venv_binaries(scratch_venv)

        print(f"=== Wheel Smoke Test: Installing {wheel_path.name} ===", flush=True)
        install_cmd = [
            "uv",
            "pip",
            "install",
            str(wheel_path),
            "--python",
            str(python_exe),
        ]
        res = subprocess.run(install_cmd, check=False)  # noqa: S603
        if res.returncode != 0:
            print("FAIL: Package installation failed", file=sys.stderr)
            return res.returncode

        print("=== Wheel Smoke Test: Testing CLI --version ===", flush=True)
        version_cmd = [str(chowki_exe), "--version"]
        res = subprocess.run(version_cmd, capture_output=True, text=True, check=False)  # noqa: S603
        if res.returncode != 0:
            print(f"FAIL: CLI --version failed: {res.stderr}", file=sys.stderr)
            return res.returncode
        print(f"CLI version output: {res.stdout.strip()}", flush=True)

        print("=== Wheel Smoke Test: Running quickstart.py ===", flush=True)
        quickstart_script = root_dir / "examples" / "python" / "quickstart.py"
        quickstart_cmd = [str(python_exe), str(quickstart_script)]
        res = subprocess.run(quickstart_cmd, cwd=root_dir, check=False)  # noqa: S603
        if res.returncode != 0:
            print("FAIL: quickstart.py execution failed", file=sys.stderr)
            return res.returncode

    print("\nWheel smoke test: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

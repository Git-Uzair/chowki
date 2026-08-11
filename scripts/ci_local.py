# scripts/ci_local.py
"""Run the exact command sequence that .github/workflows/ci.yml runs.

Keeps CI honest: if this passes locally and CI fails, the workflow file has drifted
from this script and one of the two is wrong.
"""

from __future__ import annotations

import subprocess
import sys

STEPS: list[tuple[str, list[str]]] = [
    ("layout", [sys.executable, "scripts/check_layout.py"]),
    ("sync", ["uv", "sync", "--locked", "--all-extras", "--dev"]),
    ("format", ["uv", "run", "ruff", "format", "--check", "."]),
    ("lint", ["uv", "run", "ruff", "check", "."]),
    ("pyright", ["uv", "run", "pyright"]),
    ("mypy", ["uv", "run", "mypy", "python/chowki/src"]),
    ("unit", ["uv", "run", "pytest", "python/chowki/tests/unit", "-q"]),
    ("integration", ["uv", "run", "pytest", "python/chowki/tests/integration", "-q"]),
    (
        "benchmarks",
        ["uv", "run", "pytest", "python/chowki/tests/benchmarks", "--benchmark-only", "-q"],
    ),
    ("wheel_smoke_test", ["uv", "run", "python", "scripts/wheel_smoke_test.py"]),
]


def main() -> int:
    for name, cmd in STEPS:
        print(f"\n=== chowki ci: {name} ===", flush=True)
        completed = subprocess.run(cmd, check=False)  # noqa: S603
        if completed.returncode != 0:
            print(f"FAIL: step {name} exited {completed.returncode}", file=sys.stderr)
            return completed.returncode
    print("\nchowki ci: all steps passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""chowki — in-process agent state preservation, guardrails, and warm resume."""

from __future__ import annotations

try:
    from chowki._version import __version__
except ImportError:  # pragma: no cover - source tree without a build
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]

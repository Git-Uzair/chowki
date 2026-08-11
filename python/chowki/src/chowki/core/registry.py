"""Process-global workflow registry for chowki."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

_WORKFLOW_REGISTRY: dict[str, Callable[..., Any]] = {}


def register_workflow(name: str, fn: Callable[..., Any]) -> None:
    """Register a workflow function under a string name.

    If a different function is already registered under the same name, a warning is logged
    and the entry is replaced.
    """
    existing = _WORKFLOW_REGISTRY.get(name)
    if existing is not None and existing is not fn:
        logger = structlog.get_logger()
        logger.warning("chowki_workflow_reregistered", name=name)
    _WORKFLOW_REGISTRY[name] = fn


def get_workflow(name: str) -> Callable[..., Any] | None:
    """Look up a registered workflow function by name."""
    return _WORKFLOW_REGISTRY.get(name)


def registered_workflows() -> dict[str, Callable[..., Any]]:
    """Return a shallow copy of the workflow registry dict."""
    return _WORKFLOW_REGISTRY.copy()


def clear_registry() -> None:
    """Clear all registered workflows."""
    _WORKFLOW_REGISTRY.clear()

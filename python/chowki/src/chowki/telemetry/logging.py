"""Structured logging configuration for chowki using structlog."""

from __future__ import annotations

import logging
import sys

import structlog


def _add_logger_name(
    logger: structlog.types.WrappedLogger,
    method_name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    if "logger" not in event_dict:
        name = getattr(logger, "name", None)
        event_dict["logger"] = name if name else "chowki"
    return event_dict


def configure_logging(environment: str = "production", log_level: str = "INFO") -> None:
    """Configure structlog for application logging.

    chowki never calls this automatically; application code calls it explicitly.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        _add_logger_name,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if environment == "production":
        processors: list[structlog.types.Processor] = [
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [*shared_processors, structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )

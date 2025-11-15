"""
Utilities for configuring consistent logging across Quadracode runtimes.

This module centralizes the setup of Python's logging subsystem so that every
service (agent, orchestrator, human-clone) emits structured, unbuffered logs to
stdout.  The log level and format are configurable via environment variables:

- ``QUADRACODE_LOG_LEVEL`` controls the root log level (default: ``INFO``).
- ``QUADRACODE_LOG_FORMAT`` controls the message format.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Final

DEFAULT_FORMAT: Final[str] = os.environ.get(
    "QUADRACODE_LOG_FORMAT",
    "%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
DEFAULT_DATEFMT: Final[str] = "%Y-%m-%d %H:%M:%S"
_CONFIGURED: bool = False


def _resolve_level(name: str | None) -> int:
    if not name:
        return logging.INFO
    normalized = name.strip().upper()
    return getattr(logging, normalized, logging.INFO)


def configure_logging(*, force: bool = False) -> None:
    """
    Configure the root logger to stream structured messages to stdout.

    Args:
        force: When True, existing handlers are cleared before configuring.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    root_logger = logging.getLogger()
    if force:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)

    level = _resolve_level(os.environ.get("QUADRACODE_LOG_LEVEL"))
    root_logger.setLevel(level)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT))
    root_logger.addHandler(handler)

    # Quiet down noisy dependencies unless explicitly overridden.
    logging.getLogger("httpx").setLevel(max(logging.WARNING, level))
    logging.getLogger("langchain").setLevel(max(logging.INFO, level))

    _CONFIGURED = True


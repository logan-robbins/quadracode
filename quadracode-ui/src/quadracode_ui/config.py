"""
Configuration module for Quadracode UI.

Centralizes all environment variable reads and configuration constants.

Mock Mode (QUADRACODE_MOCK_MODE=true):
- Uses fakeredis for in-memory Redis operations
- Mocks agent registry API responses
- Enables standalone UI testing without external dependencies
"""

import os
from pathlib import Path


def _int_env(var_name: str, default: int) -> int:
    """
    Safely reads an integer value from an environment variable.

    If the environment variable is not set or cannot be parsed as an integer,
    the default value is returned.
    """
    value = os.environ.get(var_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bool_env(var_name: str, default: bool = False) -> bool:
    """
    Safely reads a boolean value from an environment variable.

    Returns True for "true", "1", "yes", "on" (case insensitive).
    """
    value = os.environ.get(var_name, "").lower().strip()
    if not value:
        return default
    return value in ("true", "1", "yes", "on")


# Mock mode - enables standalone operation without Redis/agent-registry
MOCK_MODE = _bool_env("QUADRACODE_MOCK_MODE", False)

# Redis configuration
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# Agent Registry configuration
AGENT_REGISTRY_URL = os.environ.get("AGENT_REGISTRY_URL", "")

# UI mode configuration
UI_BARE = os.environ.get("UI_BARE", "0") == "1"

# Streams configuration
CONTEXT_METRICS_STREAM = os.environ.get("CONTEXT_METRICS_STREAM", "qc:context:metrics")
CONTEXT_METRICS_LIMIT = int(os.environ.get("CONTEXT_METRICS_LIMIT", "200"))
AUTONOMOUS_EVENTS_STREAM = os.environ.get("AUTONOMOUS_EVENTS_STREAM", "qc:autonomous:events")
AUTONOMOUS_EVENTS_LIMIT = int(os.environ.get("AUTONOMOUS_EVENTS_LIMIT", "200"))

# Workspace configuration
WORKSPACE_EXPORT_ROOT = Path(os.environ.get("QUADRACODE_WORKSPACE_EXPORT_ROOT", "./workspace_exports")).expanduser()
WORKSPACE_LOG_TAIL_LINES = _int_env("QUADRACODE_WORKSPACE_LOG_TAIL_LINES", 400)
WORKSPACE_LOG_LIST_LIMIT = _int_env("QUADRACODE_WORKSPACE_LOG_LIST_LIMIT", 20)
WORKSPACE_STREAM_PREFIX = os.environ.get("QUADRACODE_WORKSPACE_STREAM_PREFIX", "qc:workspace")
WORKSPACE_EVENTS_LIMIT = _int_env("QUADRACODE_WORKSPACE_EVENTS_LIMIT", 50)

# UI Settings
UI_POLL_INTERVAL_MS = int(os.environ.get("UI_POLL_INTERVAL_MS", "2000"))
UI_AUTO_REFRESH = os.environ.get("UI_AUTO_REFRESH", "true").lower() == "true"
UI_MESSAGE_PAGE_SIZE = int(os.environ.get("UI_MESSAGE_PAGE_SIZE", "50"))



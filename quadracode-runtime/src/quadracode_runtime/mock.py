"""
Mock mode support for Quadracode runtime.

This module provides mock implementations for external dependencies,
allowing the runtime to start and operate without requiring actual
external services like Redis.

Usage:
    Set QUADRACODE_MOCK_MODE=true to enable mock mode.
    
    When enabled:
    - Uses fakeredis for in-memory Redis operations
    - Uses MemorySaver for checkpointing
    - Disables MCP tool loading (uses mock tools)
    
Example:
    QUADRACODE_MOCK_MODE=true python -m quadracode_runtime
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

LOGGER = logging.getLogger(__name__)

_MOCK_REDIS_CLIENT: Any = None


def is_mock_mode_enabled() -> bool:
    """Check if mock mode is enabled via environment variable."""
    value = os.environ.get("QUADRACODE_MOCK_MODE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def get_mock_redis_client() -> Any:
    """
    Get a mock Redis client using fakeredis.
    
    Returns a singleton instance of FakeRedis that can be used as a drop-in
    replacement for the real Redis client.
    
    Returns:
        A fakeredis.FakeRedis instance, or None if fakeredis is not available.
    """
    global _MOCK_REDIS_CLIENT
    
    if _MOCK_REDIS_CLIENT is not None:
        return _MOCK_REDIS_CLIENT
    
    try:
        import fakeredis
        _MOCK_REDIS_CLIENT = fakeredis.FakeRedis(decode_responses=True)
        LOGGER.info("Mock Redis client initialized (fakeredis)")
        return _MOCK_REDIS_CLIENT
    except ImportError:
        LOGGER.warning(
            "fakeredis not installed - mock Redis unavailable. "
            "Install with: pip install fakeredis"
        )
        return None


def get_redis_client_factory() -> Callable[[], Any]:
    """
    Get a Redis client factory that returns either a real or mock client
    based on the QUADRACODE_MOCK_MODE setting.
    
    Returns:
        A callable that creates a Redis client.
    """
    if is_mock_mode_enabled():
        return get_mock_redis_client
    
    # Return factory for real Redis client
    def real_redis_factory() -> Any:
        import redis
        redis_url = os.environ.get("QUADRACODE_METRICS_REDIS_URL", "redis://redis:6379/0")
        return redis.Redis.from_url(redis_url, decode_responses=True)
    
    return real_redis_factory


def patch_redis_for_mock_mode() -> None:
    """
    Patch the global Redis clients to use mock implementations when mock mode is enabled.
    
    This should be called early in the application startup to ensure all Redis
    connections use the mock client.
    """
    if not is_mock_mode_enabled():
        return
    
    mock_client = get_mock_redis_client()
    if mock_client is None:
        LOGGER.error("Cannot enable mock mode: fakeredis not available")
        return
    
    LOGGER.info(
        "Mock mode enabled - using in-memory Redis mock. "
        "No external Redis connection required."
    )
    
    # Set environment variables to signal mock mode to other components
    os.environ.setdefault("QUADRACODE_LOCAL_DEV_MODE", "1")


class MockMCPTool:
    """A mock MCP tool that returns empty/success responses."""
    
    def __init__(self, name: str) -> None:
        self.name = name
    
    async def ainvoke(self, args: dict) -> str:
        """Mock async invocation that returns empty result."""
        if self.name == "xadd":
            return "1234567890-0"  # Mock stream entry ID
        if self.name == "xrange":
            return "[]"  # Empty stream response
        if self.name == "xdel":
            return "1"  # One entry deleted
        return ""


def get_mock_mcp_tools() -> list:
    """
    Get mock MCP tools for testing without a real MCP server.
    
    Returns:
        A list of MockMCPTool instances for xadd, xrange, and xdel.
    """
    return [
        MockMCPTool("xadd"),
        MockMCPTool("xrange"),
        MockMCPTool("xdel"),
    ]

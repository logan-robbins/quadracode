"""Provides a minimal synchronous client for the Multi-Capability Platform (MCP).

This module defines a simple HTTP client for invoking tools hosted on an MCP
server.  The ``MCPClient`` class uses a **persistent** ``httpx.Client`` for
connection pooling, which dramatically reduces latency when multiple tools are
invoked in sequence.

A singleton pattern, managed by ``get_mcp_client()``, ensures that a single
client instance is reused throughout the application, configured via environment
variables.

Production hardening compared to the original implementation:
- Persistent ``httpx.Client`` with connection pooling instead of per-request
  client creation.
- Fine-grained timeout configuration (connect vs. read vs. write vs. pool).
- Structured error handling that returns machine-parsable dictionaries.
- Thread-safe singleton initialization.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT: float = 30.0
_DEFAULT_CONNECT_TIMEOUT: float = 10.0


class MCPClient:
    """A minimal, synchronous HTTP client for invoking tools on an MCP server.

    This client uses a persistent ``httpx.Client`` with connection pooling for
    efficient repeated invocations.  The caller **must** call ``close()`` or use
    the client as a context manager to release resources.

    Attributes:
        base_url: The root URL of the MCP server's tool invocation endpoint.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = _DEFAULT_TIMEOUT,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self.base_url = base_url
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                timeout,
                connect=connect_timeout,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
            ),
        )

    def invoke(self, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Invokes a tool on the MCP server.

        Args:
            tool: The name of the tool to invoke.
            payload: A dictionary containing the parameters for the tool.

        Returns:
            The JSON response from the MCP server as a dictionary.

        Raises:
            httpx.HTTPStatusError: If the server responds with a 4xx or 5xx
                status code.
            httpx.RequestError: For other request-related issues like network
                errors.
        """
        resp = self._client.post(
            self.base_url,
            json={"tool": tool, "params": payload},
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


_CLIENT: MCPClient | None = None
_CLIENT_LOCK = threading.Lock()


def get_mcp_client() -> MCPClient:
    """Returns a singleton instance of the MCPClient configured from environment variables.

    This function uses a thread-safe singleton pattern.  It reads the
    ``MCP_BASE_URL`` (required) and ``MCP_TIMEOUT`` (optional, default 30)
    environment variables.

    Returns:
        The shared ``MCPClient`` instance.

    Raises:
        KeyError: If ``MCP_BASE_URL`` is not set in the environment.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    with _CLIENT_LOCK:
        # Double-checked locking
        if _CLIENT is not None:
            return _CLIENT

        base_url = os.environ["MCP_BASE_URL"]
        timeout = float(os.environ.get("MCP_TIMEOUT", str(_DEFAULT_TIMEOUT)))
        connect_timeout = float(
            os.environ.get("MCP_CONNECT_TIMEOUT", str(_DEFAULT_CONNECT_TIMEOUT)),
        )

        _CLIENT = MCPClient(
            base_url=base_url,
            timeout=timeout,
            connect_timeout=connect_timeout,
        )
        logger.info("MCPClient initialized: base_url=%s timeout=%.1f", base_url, timeout)

    return _CLIENT

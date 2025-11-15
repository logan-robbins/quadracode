"""Provides a minimal synchronous client for the Multi-Capability Platform (MCP).

This module defines a simple HTTP client for invoking tools hosted on an MCP
server. The `MCPClient` class encapsulates the logic for making POST requests to
the MCP's tool invocation endpoint. It is designed to be a straightforward,
no-frills client without complex features like automatic retries or fallbacks.
A singleton pattern, managed by `get_mcp_client()`, is used to ensure that a
single client instance is reused throughout the application, configured via
environment variables. This client is the bridge between a Quadracode agent and
external tools or services exposed through the MCP.
"""
from __future__ import annotations

import os
from typing import Any, Dict

import httpx


class MCPClient:
    """A minimal, synchronous HTTP client for invoking tools on an MCP server.

    This client provides a single method, `invoke`, which sends a POST request
    to a configured MCP base URL. It does not implement any sophisticated features
    like connection pooling, retries, or circuit breaking, prioritizing simplicity
    for environments where such features are handled by a service mesh or are not
    required.

    Attributes:
        base_url: The root URL of the MCP server's tool invocation endpoint.
        timeout: The request timeout in seconds.
    """

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def invoke(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Invokes a tool on the MCP server.

        Args:
            tool: The name of the tool to invoke.
            payload: A dictionary containing the parameters for the tool.

        Returns:
            The JSON response from the MCP server as a dictionary.

        Raises:
            httpx.HTTPStatusError: If the server responds with a 4xx or 5xx status code.
            httpx.RequestError: For other request-related issues like network errors.
        """
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.base_url, json={"tool": tool, "payload": payload, "params": payload})
            resp.raise_for_status()
            return resp.json()


_CLIENT: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    """Returns a singleton instance of the MCPClient, configured from environment variables.

    This function implements a singleton pattern to ensure that only one instance
    of the `MCPClient` is created and shared within the application. It reads the
    `MCP_BASE_URL` and `MCP_TIMEOUT` environment variables for configuration. The
    `MCP_BASE_URL` is required and will raise a `KeyError` if not set.

    Returns:
        The shared `MCPClient` instance.
    """
    global _CLIENT
    if _CLIENT is None:
        base_url = os.environ["MCP_BASE_URL"]  # must be provided
        timeout = float(os.environ.get("MCP_TIMEOUT", "10"))
        _CLIENT = MCPClient(base_url=base_url, timeout=timeout)
    return _CLIENT


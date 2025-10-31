from __future__ import annotations

import os
from typing import Any, Dict

import httpx


class MCPClient:
    """Minimal synchronous MCP client. No retries, no fallbacks."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def invoke(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(self.base_url, json={"tool": tool, "payload": payload, "params": payload})
            resp.raise_for_status()
            return resp.json()


_CLIENT: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    global _CLIENT
    if _CLIENT is None:
        base_url = os.environ["MCP_BASE_URL"]  # must be provided
        timeout = float(os.environ.get("MCP_TIMEOUT", "10"))
        _CLIENT = MCPClient(base_url=base_url, timeout=timeout)
    return _CLIENT


from __future__ import annotations

import os
from typing import Any, Callable, Dict, List

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

_CLIENT: MultiServerMCPClient | None = None


def _require_env(var: str) -> str:
    value = os.environ.get(var)
    if value is None or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable '{var}' for MCP configuration."
        )
    return value


def _filesystem_server() -> Dict[str, Any]:
    root = _require_env("SHARED_PATH")
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", root],
        "transport": "stdio",
    }


def _memory_server() -> Dict[str, Any]:
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "transport": "stdio",
    }


def _perplexity_server() -> Dict[str, Any]:
    api_key = _require_env("PERPLEXITY_API_KEY")
    return {
        "command": "npx",
        "args": ["-y", "server-perplexity-ask"],
        "transport": "stdio",
        "env": {"PERPLEXITY_API_KEY": api_key},
    }


def _redis_server() -> Dict[str, Any]:
    url = _require_env("MCP_REDIS_SERVER_URL")
    transport = _require_env("MCP_REDIS_TRANSPORT")
    return {"url": url, "transport": transport}


_SERVER_BUILDERS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "filesystem": _filesystem_server,
    "memory": _memory_server,
    "perplexity": _perplexity_server,
    "redis": _redis_server,
}


def _build_server_config() -> Dict[str, Dict[str, Any]]:
    config: Dict[str, Dict[str, Any]] = {}
    for name, builder in _SERVER_BUILDERS.items():
        config[name] = builder()
    return config


async def _ensure_client() -> MultiServerMCPClient:
    global _CLIENT
    if _CLIENT is None:
        config = _build_server_config()
        _CLIENT = MultiServerMCPClient(config)
    return _CLIENT


async def aget_mcp_tools() -> List[BaseTool]:
    client = await _ensure_client()
    return await client.get_tools()


def load_mcp_tools_sync() -> List[BaseTool]:
    import anyio

    return anyio.run(aget_mcp_tools)

"""
This module is responsible for loading tools from Model-Centric Programming (MCP) 
servers, allowing the Quadracode runtime to dynamically extend its capabilities.

It provides functions to build the MCP server configurations from environment 
variables, initialize a `MultiServerMCPClient`, and then load the tools that are 
exposed by those servers. The module supports both synchronous and asynchronous 
loading, making it compatible with different parts of the runtime. This dynamic 
tool loading mechanism is a key feature of the Quadracode system, enabling it to 
adapt to different environments and requirements without code changes.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

_CLIENT: MultiServerMCPClient | None = None


def _require_env(var: str) -> str:
    """
    Retrieves a required environment variable, raising an error if it's not set.
    """
    value = os.environ.get(var)
    if value is None or not value.strip():
        raise RuntimeError(
            f"Missing required environment variable '{var}' for MCP configuration."
        )
    return value


def _filesystem_server() -> Dict[str, Any]:
    """Builds the configuration for the filesystem MCP server."""
    root = _require_env("SHARED_PATH")
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", root],
        "transport": "stdio",
    }


def _memory_server() -> Dict[str, Any]:
    """Builds the configuration for the in-memory MCP server."""
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "transport": "stdio",
    }


def _perplexity_server() -> Dict[str, Any]:
    """Builds the configuration for the Perplexity MCP server."""
    api_key = _require_env("PERPLEXITY_API_KEY")
    return {
        "command": "npx",
        "args": ["-y", "server-perplexity-ask"],
        "transport": "stdio",
        "env": {"PERPLEXITY_API_KEY": api_key},
    }


def _redis_server() -> Dict[str, Any]:
    """Builds the configuration for the Redis MCP server."""
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
    """
    Builds the full MCP server configuration by calling all the individual 
    server builders.
    """
    config: Dict[str, Dict[str, Any]] = {}
    for name, builder in _SERVER_BUILDERS.items():
        config[name] = builder()
    return config


async def _ensure_client() -> MultiServerMCPClient:
    """
    Initializes and returns a singleton `MultiServerMCPClient` instance.
    """
    global _CLIENT
    if _CLIENT is None:
        config = _build_server_config()
        _CLIENT = MultiServerMCPClient(config)
    return _CLIENT


async def aget_mcp_tools() -> List[BaseTool]:
    """
    Asynchronously loads all tools from the configured MCP servers.
    """
    client = await _ensure_client()
    return await client.get_tools()


def load_mcp_tools_sync() -> List[BaseTool]:
    """
    Synchronously loads all tools from the configured MCP servers.

    This is a convenience wrapper around `aget_mcp_tools` for use in synchronous 
    code.
    """
    import anyio

    return anyio.run(aget_mcp_tools)

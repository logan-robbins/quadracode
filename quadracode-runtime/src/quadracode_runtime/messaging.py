"""
This module provides a high-level, asynchronous messaging interface, 
`RedisMCPMessaging`, which is built on top of the Redis tools exposed by an MCP 
(Model-Centric Programming) server.

It abstracts away the low-level details of interacting with the Redis MCP tools, 
providing a clean and simple API for publishing, reading, and deleting messages 
from Redis streams. The module is designed to be a self-contained component for 
all Redis-based messaging in the Quadracode runtime, ensuring that all 
interactions with the message bus are consistent and robust.

Supports mock mode for standalone testing via QUADRACODE_MOCK_MODE=true.
"""
from __future__ import annotations

import ast
from typing import Dict, List, Tuple

from langchain_core.tools import BaseTool

from quadracode_contracts import MessageEnvelope, mailbox_key

from .tools.mcp_loader import aget_mcp_tools
from .mock_mode import is_mock_mode, get_mock_redis_tools, MockRedisMCPMessaging

_REQUIRED_TOOLS = {"xadd", "xrange", "xdel"}
_TOOL_CACHE: Dict[str, BaseTool] | None = None


async def _ensure_tool_cache() -> Dict[str, BaseTool]:
    """
    Initializes and caches the required Redis MCP tools.

    This function ensures that the necessary Redis tools (`xadd`, `xrange`, `xdel`) 
    are loaded from the MCP server and are available for use. It caches the 
    tools to avoid the overhead of repeated loading.
    
    In mock mode, uses in-memory mock tools instead of real MCP tools.
    """
    global _TOOL_CACHE
    if _TOOL_CACHE is None:
        # Use mock tools in mock mode
        if is_mock_mode():
            mock_tools = get_mock_redis_tools()
            _TOOL_CACHE = {tool.name: tool for tool in mock_tools}
            return _TOOL_CACHE
        
        tools = await aget_mcp_tools()
        tool_map = {tool.name: tool for tool in tools if tool.name in _REQUIRED_TOOLS}
        missing = _REQUIRED_TOOLS - tool_map.keys()
        if missing:
            raise RuntimeError(
                "Missing required Redis MCP tool(s): "
                + ", ".join(sorted(missing))
            )
        _TOOL_CACHE = tool_map
    return _TOOL_CACHE


def _parse_stream_response(raw) -> List[Tuple[str, MessageEnvelope]]:
    """
    Parses the response from an `xrange` command into a list of message envelopes.
    Handles both string responses (for parsing) and pre-parsed list responses.
    """
    if not raw:
        return []
    
    # Handle already-parsed list response (from newer MCP/Redis clients)
    if isinstance(raw, list):
        parsed = raw
    elif isinstance(raw, str):
        if raw.startswith("Stream "):
            return []
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    else:
        return []

    entries: List[Tuple[str, MessageEnvelope]] = []
    for item in parsed:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        entry_id, fields = item
        if not isinstance(entry_id, str) or not isinstance(fields, dict):
            continue
        envelope = MessageEnvelope.from_stream_fields(fields)
        entries.append((entry_id, envelope))
    return entries


class RedisMCPMessaging:
    """
    Provides a high-level, asynchronous interface for interacting with the 
    Redis message bus via MCP tools.

    This class encapsulates the logic for publishing, reading, and deleting 
    messages from Redis streams, using the underlying `xadd`, `xrange`, and 
    `xdel` MCP tools.

    Attributes:
        _xadd: The `xadd` tool.
        _xrange: The `xrange` tool.
        _xdel: The `xdel` tool.
    """

    def __init__(self, tools: Dict[str, BaseTool]):
        """
        Initializes the `RedisMCPMessaging` instance.

        Args:
            tools: A dictionary of the required Redis MCP tools.
        """
        self._xadd = tools["xadd"]
        self._xrange = tools["xrange"]
        self._xdel = tools["xdel"]

    @classmethod
    async def create(cls) -> "RedisMCPMessaging":
        """
        Asynchronously creates and initializes a new `RedisMCPMessaging` 
        instance.
        
        In mock mode, returns a MockRedisMCPMessaging instance instead.
        """
        # Return mock implementation in mock mode
        if is_mock_mode():
            return await MockRedisMCPMessaging.create()  # type: ignore[return-value]
        
        tools = await _ensure_tool_cache()
        return cls(tools)

    async def publish(self, recipient: str, envelope: MessageEnvelope) -> str:
        """
        Publishes a message envelope to a recipient's mailbox (a Redis stream).

        Args:
            recipient: The ID of the recipient.
            envelope: The `MessageEnvelope` to be published.

        Returns:
            The ID of the newly created stream entry.
        """
        stream_key = mailbox_key(recipient)
        return await self._xadd.ainvoke(
            {"key": stream_key, "fields": envelope.to_stream_fields()}
        )

    async def read(
        self, recipient: str, *, batch_size: int = 10
    ) -> List[Tuple[str, MessageEnvelope]]:
        """
        Reads a batch of messages from a recipient's mailbox.

        Args:
            recipient: The ID of the recipient.
            batch_size: The maximum number of messages to read.

        Returns:
            A list of tuples, each containing a stream entry ID and the 
            corresponding `MessageEnvelope`.
        """
        stream_key = mailbox_key(recipient)
        response = await self._xrange.ainvoke({"key": stream_key, "count": batch_size})
        return _parse_stream_response(response)

    async def delete(self, recipient: str, entry_id: str) -> str:
        """
        Deletes a specific message from a recipient's mailbox.

        Args:
            recipient: The ID of the recipient.
            entry_id: The ID of the stream entry to be deleted.

        Returns:
            The number of entries deleted (as a string).
        """
        stream_key = mailbox_key(recipient)
        return await self._xdel.ainvoke({"key": stream_key, "entry_id": entry_id})

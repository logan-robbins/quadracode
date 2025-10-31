from __future__ import annotations

import ast
from typing import Dict, List, Tuple

from langchain_core.tools import BaseTool

from quadracode_contracts import MessageEnvelope, mailbox_key

from .tools.mcp_loader import aget_mcp_tools

_REQUIRED_TOOLS = {"xadd", "xrange", "xdel"}
_TOOL_CACHE: Dict[str, BaseTool] | None = None


async def _ensure_tool_cache() -> Dict[str, BaseTool]:
    global _TOOL_CACHE
    if _TOOL_CACHE is None:
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


def _parse_stream_response(raw: str) -> List[Tuple[str, MessageEnvelope]]:
    if not raw or raw.startswith("Stream "):
        return []
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
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
    def __init__(self, tools: Dict[str, BaseTool]):
        self._xadd = tools["xadd"]
        self._xrange = tools["xrange"]
        self._xdel = tools["xdel"]

    @classmethod
    async def create(cls) -> "RedisMCPMessaging":
        tools = await _ensure_tool_cache()
        return cls(tools)

    async def publish(self, recipient: str, envelope: MessageEnvelope) -> str:
        stream_key = mailbox_key(recipient)
        return await self._xadd.ainvoke(
            {"key": stream_key, "fields": envelope.to_stream_fields()}
        )

    async def read(
        self, recipient: str, *, batch_size: int = 10
    ) -> List[Tuple[str, MessageEnvelope]]:
        stream_key = mailbox_key(recipient)
        response = await self._xrange.ainvoke({"key": stream_key, "count": batch_size})
        return _parse_stream_response(response)

    async def delete(self, recipient: str, entry_id: str) -> str:
        stream_key = mailbox_key(recipient)
        return await self._xdel.ainvoke({"key": stream_key, "entry_id": entry_id})

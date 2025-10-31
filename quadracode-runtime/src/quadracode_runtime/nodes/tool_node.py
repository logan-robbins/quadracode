from __future__ import annotations

from datetime import datetime
import time
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from ..tools.shared import load_shared_tools
from ..tools.mcp_loader import load_mcp_tools_sync


@tool
def get_weather(city: str) -> str:
    """Return a playful weather summary for the requested city."""
    return f"It's always sunny in {city}!"


@tool
def get_time_pst() -> str:
    """Return the current time in the US Pacific timezone."""
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


@tool
def get_time_est() -> str:
    """Return the current time in the US Eastern timezone."""
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


@tool
def wait(duration: int) -> str:
    """Block for `duration` seconds and report completion."""
    time.sleep(duration)
    return f"slept {duration}"


LOCAL_TOOL_DEFINITIONS = [get_weather, get_time_pst, get_time_est, wait]
SHARED_TOOL_DEFINITIONS = load_shared_tools()
MCP_TOOL_DEFINITIONS = load_mcp_tools_sync()

_mcp_names = {getattr(t, "name", None) for t in MCP_TOOL_DEFINITIONS}
_shared_filtered = [
    t for t in SHARED_TOOL_DEFINITIONS if getattr(t, "name", None) not in _mcp_names
]

ALL_TOOL_DEFINITIONS = [*LOCAL_TOOL_DEFINITIONS, *_shared_filtered, *MCP_TOOL_DEFINITIONS]

QuadracodeTools = ToolNode(ALL_TOOL_DEFINITIONS)
QuadracodeTools.tools = ALL_TOOL_DEFINITIONS

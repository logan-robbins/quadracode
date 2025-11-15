"""
This module is responsible for aggregating all the available tools from various 
sources and creating a unified `ToolNode` for the LangGraph.

The `ToolNode` is a specialized node in the graph that is responsible for 
executing tool calls. This module collects tools from three main sources: local 
definitions, shared tools from the `quadracode-tools` package, and tools loaded 
from MCP (Model-Centric Programming) servers. It then de-duplicates these tools 
and creates a single `ToolNode` that can be used in the main graph.
"""
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
    """
    Returns a playful, hardcoded weather summary for a given city.

    This tool is primarily for testing and demonstration purposes.

    Args:
        city: The name of the city.

    Returns:
        A string with a sunny weather forecast.
    """
    return f"It's always sunny in {city}!"


@tool
def get_time_pst() -> str:
    """
    Returns the current time in the US Pacific timezone (PST).
    """
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


@tool
def get_time_est() -> str:
    """
    Returns the current time in the US Eastern timezone (EST).
    """
    now = datetime.now(ZoneInfo("America/New_York"))
    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


@tool
def wait(duration: int) -> str:
    """
    Blocks execution for a specified number of seconds.

    This is a simple utility tool for testing asynchronous behavior and delays.

    Args:
        duration: The number of seconds to wait.

    Returns:
        A string confirming the duration of the wait.
    """
    time.sleep(duration)
    return f"slept {duration}"


LOCAL_TOOL_DEFINITIONS = [get_weather, get_time_pst, get_time_est, wait]
SHARED_TOOL_DEFINITIONS = load_shared_tools()
MCP_TOOL_DEFINITIONS = load_mcp_tools_sync()

# De-duplicate tools, giving precedence to MCP-loaded tools over shared tools
_mcp_names = {getattr(t, "name", None) for t in MCP_TOOL_DEFINITIONS}
_shared_filtered = [
    t for t in SHARED_TOOL_DEFINITIONS if getattr(t, "name", None) not in _mcp_names
]

ALL_TOOL_DEFINITIONS = [*LOCAL_TOOL_DEFINITIONS, *_shared_filtered, *MCP_TOOL_DEFINITIONS]

# Create the unified ToolNode
QuadracodeTools = ToolNode(ALL_TOOL_DEFINITIONS)
QuadracodeTools.tools = ALL_TOOL_DEFINITIONS

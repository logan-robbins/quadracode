"""
This module provides the utility for loading shared tool definitions from the 
`quadracode_tools` package.

The `quadracode_tools` package is a dedicated library for reusable tools that can 
be used by both the orchestrator and the agents. This module provides a robust 
mechanism for dynamically importing and loading these tools at runtime. It is 
designed to be resilient to import errors and to support multiple conventions for 
exposing the tool definitions, making the tool loading process more flexible and 
maintainable.
"""
from __future__ import annotations

from typing import Any, List


def load_shared_tools() -> List[Any]:
    """
    Dynamically loads shared tool definitions from the `quadracode_tools` package.

    This function attempts to import the `quadracode_tools` package and then 
    looks for tool definitions exposed via either a `get_tools` function or a 
    `TOOL_DEFINITIONS` list. This flexible loading mechanism allows the 
    `quadracode_tools` package to evolve its conventions without breaking the 
    runtime.

    Returns:
        A list of the loaded tool definitions.
    """
    try:
        import quadracode_tools as qtools  # type: ignore
    except Exception:
        return []

    tools: List[Any] = []

    # Primary loading mechanism: get_tools() function
    get_tools = getattr(qtools, "get_tools", None)
    if callable(get_tools):
        try:
            result = get_tools()
            if isinstance(result, list):
                tools.extend(result)
        except Exception:
            pass

    # Fallback loading mechanism: TOOL_DEFINITIONS list
    if not tools:
        fallback = getattr(qtools, "TOOL_DEFINITIONS", None)
        if isinstance(fallback, list):
            tools.extend(fallback)

    return tools

from __future__ import annotations

from typing import Any, List


def load_shared_tools() -> List[Any]:
    """Load shared tool definitions from the quadracode_tools package."""
    try:
        import quadracode_tools as qtools  # type: ignore
    except Exception:
        return []

    tools: List[Any] = []

    get_tools = getattr(qtools, "get_tools", None)
    if callable(get_tools):
        try:
            result = get_tools()
            if isinstance(result, list):
                tools.extend(result)
        except Exception:
            pass

    if not tools:
        fallback = getattr(qtools, "TOOL_DEFINITIONS", None)
        if isinstance(fallback, list):
            tools.extend(fallback)

    return tools

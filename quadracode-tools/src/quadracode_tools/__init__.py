"""Quadracode shared tools (simple, explicit, file-per-tool).

Public API:
- get_tools() -> list[Tool]
"""

from .assembly import get_tools

__all__ = ["get_tools"]


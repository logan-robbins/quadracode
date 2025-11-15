"""
This package provides the utilities for loading and managing the tools that are 
available to the Quadracode runtime.

It serves as the central hub for tool discovery, aggregating tools from various 
sources, including the shared `quadracode-tools` package and any configured MCP 
(Model-Centric Programming) servers. By exposing a set of loader functions, this 
package provides a unified and extensible mechanism for making tools available to 
the LangGraph.
"""
from .shared import load_shared_tools
from .mcp_loader import load_mcp_tools_sync, aget_mcp_tools

__all__ = ["load_shared_tools", "load_mcp_tools_sync", "aget_mcp_tools"]

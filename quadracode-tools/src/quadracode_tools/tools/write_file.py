"""Provides a LangChain tool for writing text content to a file.

This module contains the `write_file` tool, which is a counterpart to `read_file`.
It allows an agent to create or overwrite a file with specified text content.
This capability is fundamental for tasks that involve code generation, configuration
management, or saving the results of a computation. The tool automatically creates
any necessary parent directories, simplifying its use for agents that may not have
full awareness of the filesystem's state.
"""
from __future__ import annotations

from pathlib import Path
from langchain_core.tools import tool


@tool
def write_file(path: str, content: str) -> str:
    """Writes a string to a UTF-8 encoded text file, overwriting it if it exists.

    This tool is a basic filesystem operation that allows an agent to persist a
    string as the content of a file. It is designed to be a simple and reliable
    way to modify the filesystem.

    Key features:
    - **Directory Creation**: If the parent directories for the specified `path` do
      not exist, they will be created automatically.
    - **Overwrite by Default**: If a file already exists at the given path, its
      contents will be completely replaced by the new `content`.
    - **UTF-8 Encoding**: The content is always written using the UTF-8 encoding,
      which is a safe default for most text-based files.

    For operations like appending to a file or more complex modifications, agents
    should use the `bash_shell` tool with appropriate shell commands (e.g., `echo "..." >> file.log`).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return path

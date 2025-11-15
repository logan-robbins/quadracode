"""Provides a LangChain tool for reading files from the local filesystem.

This module contains the `read_file` tool, a simple but essential utility that
allows an agent to read the contents of a text file. It is a fundamental building
block for tasks that require an agent to understand the current state of a
codebase, configuration files, or log outputs. The tool is designed to be used
within the context of a containerized workspace, where file paths are relative to
the container's filesystem.
"""
from __future__ import annotations

from pathlib import Path
from langchain_core.tools import tool


@tool
def read_file(path: str) -> str:
    """Reads the entire contents of a UTF-8 encoded text file and returns it as a string.

    This tool is a basic filesystem operation that allows an agent to ingest the
    contents of a file. It is intended for reading source code, configuration files,
    logs, or any other text-based data.

    Usage notes:
    - The `path` argument must be an absolute path or a path relative to the current
      working directory of the agent's execution environment.
    - This tool reads the entire file into memory. For very large files, agents
      should be encouraged to use the `bash_shell` tool with commands like `head` or
      `tail` to read only a portion of the file.
    - The file is assumed to be UTF-8 encoded. Reading files with other encodings
      may result in a `UnicodeDecodeError`.
    """
    return Path(path).read_text(encoding="utf-8")

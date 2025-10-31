from __future__ import annotations

from pathlib import Path
from langchain_core.tools import tool


@tool
def read_file(path: str) -> str:
    """
    Read a UTF-8 text file from disk and return its contents.

    Best practices for agents:
    - Path must be accessible inside the container (respect mounts).
    - Use for configs, code, and logs up to a few MB.
    - For huge files, read a slice via `bash_shell` (e.g., `head`, `tail`).
    """
    return Path(path).read_text(encoding="utf-8")

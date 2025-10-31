from __future__ import annotations

from pathlib import Path
from langchain_core.tools import tool


@tool
def write_file(path: str, content: str) -> str:
    """
    Write UTF-8 text to disk, creating parent directories as needed. Overwrites existing files.

    Best practices for agents:
    - Use for config/code updates where full overwrite is intended.
    - For appends, use `bash_shell` with `tee -a` or `>>` redirection.
    - Keep files reasonably small; split large outputs across files if needed.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return path

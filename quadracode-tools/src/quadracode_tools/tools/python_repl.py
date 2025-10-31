from __future__ import annotations

import json
from typing import Any, Dict

from langchain_core.tools import tool


@tool
def python_repl(code: str) -> str:
    """
    Execute Python code and return locals as JSON.

    Best practices for agents:
    - Use for quick calculations, parsing, small transforms, or generating snippets.
    - Keep code pure and fast (<2s). Avoid network calls, blocking I/O, or long loops.
    - Prefer `read_file`/`write_file` for filesystem access; avoid heavy imports.
    - Assign important results to variables; returned JSON is the locals() mapping.
    - Keep output small; large objects are stringified via JSON with `default=str`.
    """
    local_vars: Dict[str, Any] = {}
    exec(code, {}, local_vars)
    return json.dumps({k: v for k, v in local_vars.items()}, default=str)

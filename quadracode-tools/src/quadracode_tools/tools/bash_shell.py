from __future__ import annotations

import json
import subprocess

from langchain_core.tools import tool


@tool
def bash_shell(command: str) -> str:
    """
    Execute a bash command and return stdout/stderr/returncode as JSON.

    Best practices for agents:
    - Use for one-shot, non-interactive commands; avoid long-running processes.
    - Prefer `read_file`/`write_file` when reading/writing files.
    - Keep outputs bounded; pipe with `| head -n 200` for large files.
    - Use absolute paths in mounted volumes; working dir is the container's CWD.
    - Do not start background daemons; this tool is for immediate, deterministic tasks.
    """
    result = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, check=False)
    return json.dumps({
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    })

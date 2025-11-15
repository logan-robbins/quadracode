"""Provides a LangChain tool for executing shell commands in a Bash login shell.

This module offers a `bash_shell` tool that enables an agent to run arbitrary
shell commands within its execution environment. This is a powerful and flexible
tool that allows for a wide range of interactions with the underlying system,
such as file manipulation, process inspection, and network diagnostics. The
command is executed in a non-interactive login shell (`bash -lc`), which ensures
that the user's profile scripts (e.g., `.bash_profile`) are sourced. The tool
captures `stdout`, `stderr`, and the exit code, returning them as a structured
JSON object.
"""
from __future__ import annotations

import json
import subprocess

from langchain_core.tools import tool


@tool
def bash_shell(command: str) -> str:
    """Executes a single bash command in a login shell and returns the output.

    This tool allows an agent to run non-interactive shell commands. The command
    is executed via `bash -lc`, which means it runs within a login shell, ensuring
    that environment variables and shell functions from startup scripts like
    `~/.bash_profile` are available.

    The result of the command is returned as a JSON string containing three keys:
    - `returncode`: The integer exit code of the command.
    - `stdout`: The standard output of the command as a string.
    - `stderr`: The standard error of the command as a string.

    Usage notes:
    - This tool is intended for short-lived, synchronous commands. Do not use it
      to start long-running processes or daemons.
    - For simple file reading and writing, prefer the `read_file` and `write_file`
      tools, as they are more explicit and less prone to shell injection issues.
    - Agents should be careful to handle commands that might produce a large amount
      of output, potentially by piping to `head` or `tail`.
    """
    result = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, check=False)
    return json.dumps({
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    })

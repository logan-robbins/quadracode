"""Provides a LangChain tool for executing sandboxed Python code snippets.

This module offers a `python_repl` tool that allows an agent to execute arbitrary
Python code within a sandboxed environment. The primary use case is for quick,
stateless computations, data transformations, or simple logic evaluation that
doesn't warrant creating a new file or a more complex tool. The `exec` function
is used to run the code, and the resulting local variables are captured and
serialized to a JSON string. This provides a simple yet powerful capability for
agents to perform dynamic calculations and scripting tasks.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from langchain_core.tools import tool


@tool
def python_repl(code: str) -> str:
    """Executes a snippet of Python code and returns the local variables as JSON.

    This tool provides a sandboxed Read-Eval-Print Loop (REPL) environment for an
    agent. It is designed for simple, synchronous tasks like calculations, data
    manipulation, or evaluating short logical expressions.

    The provided `code` is executed using Python's `exec()` function. Any variables
    defined at the top level of the script are captured from the `locals()` scope
    after execution. These variables are then serialized into a JSON object, which
    is returned as a string.

    Security and Performance Considerations:
    - The execution is sandboxed only by the scope of the `exec` call; it is not
      a secure sandbox for untrusted code.
    - Agents should be prompted to provide simple, fast-executing code. Avoid
      long-running operations, network requests, or heavy I/O, as these will block
      the agent's execution thread.
    - For filesystem interactions, agents should prefer the dedicated `read_file`
      and `write_file` tools.
    """
    local_vars: Dict[str, Any] = {}
    exec(code, {}, local_vars)
    return json.dumps({k: v for k, v in local_vars.items()}, default=str)

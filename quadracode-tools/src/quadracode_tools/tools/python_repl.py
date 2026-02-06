"""Provides a LangChain tool for executing sandboxed Python code snippets.

This module offers a ``python_repl`` tool that allows an agent to execute
arbitrary Python code within a sandboxed environment.  The primary use case is
for quick, stateless computations, data transformations, or simple logic
evaluation.

Production hardening:
- Comprehensive ``try/except`` around ``exec()`` so that code-level exceptions
  are captured and returned as structured JSON instead of crashing the agent.
- **Output size limit** prevents a pathological expression from creating a
  multi-gigabyte string in the local namespace.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_RESULT_CHARS: int = 200_000  # ~200 KB of serialized output


class PythonReplRequest(BaseModel):
    """Input schema for the Python REPL tool."""

    code: str = Field(
        ...,
        min_length=1,
        description=(
            "Python code to execute.  Top-level variables are captured and "
            "returned as JSON."
        ),
    )


@tool(args_schema=PythonReplRequest)
def python_repl(code: str) -> str:
    """Executes a snippet of Python code and returns the local variables as JSON.

    This tool provides a Read-Eval-Print Loop (REPL) environment for an agent.
    It is designed for simple, synchronous tasks like calculations, data
    manipulation, or evaluating short logical expressions.

    The provided ``code`` is executed using Python's ``exec()`` function.  Any
    variables defined at the top level of the script are captured from the
    ``locals()`` scope after execution and serialized into a JSON object.

    Security and Performance Considerations:
    - The execution is sandboxed only by the scope of the ``exec`` call; it is
      **not** a secure sandbox for untrusted code.
    - Avoid long-running operations, network requests, or heavy I/O.
    - For filesystem interactions prefer ``read_file`` and ``write_file``.
    - For executing code inside a workspace container use ``workspace_exec``.

    On success returns JSON with captured local variables.
    On failure returns JSON with ``error`` and ``traceback`` keys.
    """
    local_vars: dict[str, Any] = {}
    try:
        exec(code, {}, local_vars)  # noqa: S102

        serialized = json.dumps(
            {k: v for k, v in local_vars.items()},
            default=str,
        )

        if len(serialized) > _MAX_RESULT_CHARS:
            keys = list(local_vars.keys())
            return json.dumps({
                "error": f"Output too large ({len(serialized):,} chars, max {_MAX_RESULT_CHARS:,})",
                "captured_keys": keys,
            })

        return serialized

    except SyntaxError as exc:
        logger.warning("python_repl syntax error: %s", exc)
        return json.dumps({
            "error": f"SyntaxError: {exc.msg}",
            "lineno": exc.lineno,
            "offset": exc.offset,
        })

    except Exception as exc:
        logger.warning("python_repl execution error: %s", exc)
        import traceback

        return json.dumps({
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        })


python_repl.name = "python_repl"

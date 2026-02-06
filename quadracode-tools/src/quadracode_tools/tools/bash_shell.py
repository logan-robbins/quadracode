"""Provides a LangChain tool for executing shell commands in a Bash login shell.

This module offers a ``bash_shell`` tool that enables an agent to run arbitrary
shell commands within its execution environment.  The command is executed in a
non-interactive login shell (``bash -lc``) which sources profile scripts.  The
tool captures ``stdout``, ``stderr``, and the exit code, returning them as a
structured JSON object.

Production hardening:
- Configurable per-invocation **timeout** (default 120 s) prevents hung commands
  from blocking the agent indefinitely.
- **Output truncation** (default 100 KB per stream) prevents memory exhaustion
  when a command emits unbounded output.
- All error paths (timeout, unexpected exception) return structured JSON so that
  the agent can always parse the result.
"""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS: float = 120.0
_MAX_OUTPUT_BYTES: int = 100_000  # 100 KB per stream


class BashShellRequest(BaseModel):
    """Input schema for bash shell command execution."""

    command: str = Field(
        ...,
        min_length=1,
        description="Shell command to execute in a login bash shell.",
    )
    timeout: float = Field(
        default=_DEFAULT_TIMEOUT_SECONDS,
        ge=1.0,
        le=3600.0,
        description="Maximum execution time in seconds (default 120, max 3600).",
    )


def _truncate_output(text: str, max_bytes: int = _MAX_OUTPUT_BYTES) -> str:
    """Truncate output to *max_bytes*, keeping the **tail** (most recent output).

    Args:
        text: Raw output string from the subprocess.
        max_bytes: Maximum size in bytes after truncation.

    Returns:
        The (possibly truncated) output string.
    """
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    kept = encoded[-max_bytes:]
    trimmed_count = len(encoded) - max_bytes
    return f"[truncated {trimmed_count} bytes]\n{kept.decode('utf-8', errors='replace')}"


def _build_response(
    returncode: int,
    stdout: str,
    stderr: str,
    **extra: Any,
) -> str:
    """Build a deterministic JSON response string."""
    payload: dict[str, Any] = {
        "returncode": returncode,
        "stdout": _truncate_output(stdout),
        "stderr": _truncate_output(stderr),
    }
    payload.update(extra)
    return json.dumps(payload)


@tool(args_schema=BashShellRequest)
def bash_shell(command: str, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> str:
    """Executes a single bash command in a login shell and returns the output.

    This tool allows an agent to run non-interactive shell commands.  The command
    is executed via ``bash -lc``, which runs within a login shell, ensuring that
    environment variables and shell functions from startup scripts like
    ``~/.bash_profile`` are available.

    The result is a JSON string containing:
    - ``returncode``: The integer exit code of the command.
    - ``stdout``: The standard output (truncated to ~100 KB).
    - ``stderr``: The standard error (truncated to ~100 KB).
    - ``timed_out``: Boolean flag present when the command exceeded its timeout.

    Usage notes:
    - This tool is intended for short-lived, synchronous commands.
    - For simple file reading and writing, prefer the ``read_file`` and
      ``write_file`` tools.
    - Agents should be careful to handle commands that might produce a large
      amount of output, potentially by piping to ``head`` or ``tail``.
    """
    try:
        result = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return _build_response(result.returncode, result.stdout, result.stderr)

    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "bash_shell timed out after %.1fs: %.200s", timeout, command,
        )
        partial_stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        partial_stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return _build_response(
            -1,
            partial_stdout,
            partial_stderr + f"\n[quadracode] Command timed out after {timeout}s",
            timed_out=True,
        )

    except Exception as exc:
        logger.exception("bash_shell unexpected error: %.200s", command)
        return _build_response(
            -1, "", str(exc), error=type(exc).__name__,
        )


bash_shell.name = "bash_shell"

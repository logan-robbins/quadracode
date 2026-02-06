"""Provides a LangChain tool for reading files from the local filesystem.

This module contains the ``read_file`` tool, a simple but essential utility that
allows an agent to read the contents of a text file.  It is a fundamental
building block for tasks that require an agent to understand the current state
of a codebase, configuration files, or log outputs.

Production hardening:
- **File-size guard** (default 10 MB) prevents accidental ingestion of huge
  binary blobs that would exhaust context or memory.
- Structured error responses for missing files, encoding issues, and permission
  errors so the agent can always parse the result.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB


class ReadFileRequest(BaseModel):
    """Input schema for file reading operations."""

    path: str = Field(
        ...,
        min_length=1,
        description=(
            "Absolute or relative path to the file to read.  Must be a regular "
            "text file encoded in UTF-8."
        ),
    )


def _json_error(message: str, **extra: object) -> str:
    """Return a structured JSON error string."""
    payload: dict[str, object] = {"success": False, "error": message}
    payload.update(extra)
    return json.dumps(payload)


@tool(args_schema=ReadFileRequest)
def read_file(path: str) -> str:
    """Reads the entire contents of a UTF-8 encoded text file and returns it as a string.

    This tool is a basic filesystem operation that allows an agent to ingest the
    contents of a file.  It is intended for reading source code, configuration
    files, logs, or any other text-based data.

    Usage notes:
    - The ``path`` argument must be an absolute path or a path relative to the
      current working directory of the agent's execution environment.
    - Files larger than 10 MB are rejected.  For very large files use the
      ``bash_shell`` tool with ``head`` or ``tail``.
    - The file is assumed to be UTF-8 encoded.  Files with other encodings will
      return a structured error.

    On success the raw file contents are returned.  On failure a JSON object
    with ``success: false`` and an ``error`` key is returned.
    """
    try:
        target = Path(path)

        if not target.exists():
            return _json_error(f"File not found: {path}")

        if not target.is_file():
            return _json_error(f"Not a regular file: {path}")

        size = target.stat().st_size
        if size > _MAX_FILE_SIZE_BYTES:
            return _json_error(
                f"File too large ({size:,} bytes, max {_MAX_FILE_SIZE_BYTES:,}).  "
                "Use bash_shell with head/tail.",
                size_bytes=size,
            )

        return target.read_text(encoding="utf-8")

    except UnicodeDecodeError as exc:
        logger.warning("read_file encoding error for %s: %s", path, exc)
        return _json_error(f"Encoding error (expected UTF-8): {exc}")

    except PermissionError:
        return _json_error(f"Permission denied: {path}")

    except OSError as exc:
        logger.exception("read_file OS error for %s", path)
        return _json_error(str(exc))


read_file.name = "read_file"

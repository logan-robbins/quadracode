"""Provides a LangChain tool for writing text content to a file.

This module contains the ``write_file`` tool, which is a counterpart to
``read_file``.  It allows an agent to create or overwrite a file with specified
text content.  This capability is fundamental for tasks that involve code
generation, configuration management, or saving the results of a computation.

Production hardening:
- Structured error responses for permission issues and disk-full conditions.
- Automatic parent directory creation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class WriteFileRequest(BaseModel):
    """Input schema for file writing operations."""

    path: str = Field(
        ...,
        min_length=1,
        description="Absolute or relative path to the file to create/overwrite.",
    )
    content: str = Field(
        ...,
        description="UTF-8 text content to write to the file.",
    )


def _json_error(message: str) -> str:
    """Return a structured JSON error string."""
    return json.dumps({"success": False, "error": message})


@tool(args_schema=WriteFileRequest)
def write_file(path: str, content: str) -> str:
    """Writes a string to a UTF-8 encoded text file, overwriting it if it exists.

    This tool is a basic filesystem operation that allows an agent to persist a
    string as the content of a file.

    Key features:
    - **Directory Creation**: If the parent directories for the specified ``path``
      do not exist, they will be created automatically.
    - **Overwrite by Default**: If a file already exists at the given path, its
      contents will be completely replaced by the new ``content``.
    - **UTF-8 Encoding**: The content is always written using the UTF-8 encoding.

    On success the file path is returned.  On failure a JSON object with
    ``success: false`` and an ``error`` key is returned.
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return path

    except PermissionError:
        return _json_error(f"Permission denied: {path}")

    except OSError as exc:
        logger.exception("write_file OS error for %s", path)
        return _json_error(str(exc))


write_file.name = "write_file"

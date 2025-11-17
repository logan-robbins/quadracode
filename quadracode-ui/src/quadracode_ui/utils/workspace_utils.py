"""
Workspace management utilities for Quadracode UI.

Provides functions for workspace lifecycle operations and file access.
"""

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any

import redis
import streamlit as st

from quadracode_tools.tools.workspace import (
    workspace_copy_from,
    workspace_create,
    workspace_destroy,
    workspace_exec,
)
from quadracode_ui.config import (
    WORKSPACE_EVENTS_LIMIT,
    WORKSPACE_LOG_LIST_LIMIT,
    WORKSPACE_LOG_TAIL_LINES,
    WORKSPACE_STREAM_PREFIX,
)


def invoke_workspace_tool(
    tool: Any,
    params: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """
    Invokes a workspace tool and standardizes its output.

    Args:
        tool: The tool object to invoke (e.g., `workspace_create`).
        params: The dictionary of parameters to pass to the tool.

    Returns:
        A tuple containing:
        - A boolean indicating success.
        - The parsed JSON data from the tool's response, if any.
        - An error message string, if the operation failed.
    """
    try:
        raw_result = tool.invoke(params)
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)

    if isinstance(raw_result, dict):
        parsed = raw_result
    else:
        try:
            parsed = json.loads(raw_result or "{}")
        except json.JSONDecodeError:
            return False, None, "Workspace tool returned invalid JSON payload."

    success = bool(parsed.get("success"))
    if success:
        return True, parsed, None

    error_message = parsed.get("error")
    if not error_message:
        errors = parsed.get("errors")
        if isinstance(errors, list):
            error_message = "; ".join(str(entry) for entry in errors if entry)
    if not error_message and isinstance(parsed.get("message"), str):
        error_message = str(parsed["message"])
    if not error_message:
        error_message = "Workspace operation failed."
    return False, parsed, error_message


def create_workspace(workspace_id: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    """
    Creates a new workspace.

    Args:
        workspace_id: The ID for the new workspace.

    Returns:
        A tuple of (success, descriptor, error_message).
    """
    success, data, error = invoke_workspace_tool(
        workspace_create,
        {"workspace_id": workspace_id},
    )
    
    if success and isinstance(data, dict):
        descriptor = data.get("workspace")
        if isinstance(descriptor, dict):
            return True, descriptor, None
        return True, None, "Workspace created but descriptor was not returned."
    
    return False, None, error


def destroy_workspace(workspace_id: str, delete_volume: bool = True) -> tuple[bool, dict[str, Any] | None, str | None]:
    """
    Destroys a workspace.

    Args:
        workspace_id: The ID of the workspace to destroy.
        delete_volume: Whether to delete the associated volume.

    Returns:
        A tuple of (success, result_data, error_message).
    """
    return invoke_workspace_tool(
        workspace_destroy,
        {
            "workspace_id": workspace_id,
            "delete_volume": delete_volume,
        },
    )


def copy_from_workspace(
    workspace_id: str,
    source_path: str,
    destination_path: str,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """
    Copies files from a workspace to the host.

    Args:
        workspace_id: The workspace ID.
        source_path: The source path inside the workspace.
        destination_path: The destination path on the host.

    Returns:
        A tuple of (success, result_data, error_message).
    """
    return invoke_workspace_tool(
        workspace_copy_from,
        {
            "workspace_id": workspace_id,
            "source_path": source_path,
            "destination_path": destination_path,
        },
    )


def exec_in_workspace(workspace_id: str, command: str) -> tuple[bool, str, str]:
    """
    Executes a command inside a workspace.

    Args:
        workspace_id: The ID of the target workspace.
        command: The shell command to execute.

    Returns:
        A tuple of (success, stdout, stderr).
    """
    success, data, error_message = invoke_workspace_tool(
        workspace_exec,
        {
            "workspace_id": workspace_id,
            "command": command,
        },
    )
    
    stdout = ""
    stderr = ""
    if isinstance(data, dict):
        command_result = data.get("workspace_command")
        if isinstance(command_result, dict):
            stdout = command_result.get("stdout", "") or ""
            stderr = command_result.get("stderr", "") or ""
    
    if success:
        return True, stdout, stderr
    
    fallback = error_message or stderr
    return False, stdout, fallback


def list_workspace_logs(workspace_id: str) -> list[str]:
    """
    Lists the most recent log files in a workspace.

    Args:
        workspace_id: The ID of the workspace to query.

    Returns:
        A list of log file names.
    """
    command = (
        "if [ -d /workspace/logs ]; then "
        f"ls -1t /workspace/logs | head -{WORKSPACE_LOG_LIST_LIMIT}; "
        "fi"
    )
    success, stdout, _ = exec_in_workspace(workspace_id, command)
    if not success and not stdout:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def read_workspace_log(workspace_id: str, log_name: str) -> tuple[bool, str]:
    """
    Reads the trailing content of a log file from a workspace.

    Args:
        workspace_id: The workspace ID.
        log_name: The name of the log file under `/workspace/logs`.

    Returns:
        A tuple of (success, content).
    """
    if not log_name:
        return False, "Select a log file to preview."
    
    safe_path = shlex.quote(f"/workspace/logs/{log_name}")
    command = (
        f"if [ -f {safe_path} ]; then "
        f"tail -n {WORKSPACE_LOG_TAIL_LINES} {safe_path}; "
        "else "
        f"echo 'Log not found: {log_name}' >&2; "
        "exit 1; "
        "fi"
    )
    success, stdout, error_message = exec_in_workspace(workspace_id, command)
    if success:
        return True, stdout or "(log is empty)"
    
    detail = error_message or "Failed to load log file."
    return False, detail


def list_workspace_files(workspace_id: str) -> list[str]:
    """
    Lists all files in a workspace.

    Args:
        workspace_id: The workspace ID.

    Returns:
        A list of file paths relative to /workspace.
    """
    command = 'find /workspace -type f 2>/dev/null || true'
    success, stdout, _ = exec_in_workspace(workspace_id, command)
    if not success and not stdout:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def read_workspace_file(workspace_id: str, file_path: str) -> tuple[bool, str]:
    """
    Reads the content of a file from a workspace.

    Args:
        workspace_id: The workspace ID.
        file_path: The full path to the file inside the workspace.

    Returns:
        A tuple of (success, content).
    """
    safe_path = shlex.quote(file_path)
    command = f"cat {safe_path} 2>&1"
    success, stdout, stderr = exec_in_workspace(workspace_id, command)
    
    if success:
        return True, stdout
    return False, stderr or "Failed to read file"


def workspace_stream_key(workspace_id: str) -> str:
    """
    Constructs the Redis stream key for workspace events.

    Args:
        workspace_id: The unique identifier for the workspace.

    Returns:
        The fully-qualified Redis stream key.
    """
    suffix = workspace_id.strip()
    if not suffix:
        return ""
    return f"{WORKSPACE_STREAM_PREFIX}:{suffix}:events"


def load_workspace_events(
    client: redis.Redis,
    workspace_id: str,
    limit: int = WORKSPACE_EVENTS_LIMIT,
) -> list[dict[str, Any]]:
    """
    Loads workspace events from Redis.

    Args:
        client: The Redis client.
        workspace_id: The ID of the workspace.
        limit: Maximum number of events to load.

    Returns:
        A list of parsed event dictionaries.
    """
    if limit <= 0:
        return []
    
    stream_key = workspace_stream_key(workspace_id)
    if not stream_key:
        return []
    
    try:
        raw_entries = client.xrevrange(stream_key, count=limit)
    except redis.ResponseError:
        return []
    except redis.RedisError:
        return []

    parsed: list[dict[str, Any]] = []
    for entry_id, fields in raw_entries:
        event = fields.get("event", "unknown")
        timestamp = fields.get("timestamp")
        payload_raw = fields.get("payload")
        
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {"raw_payload": payload_raw}
        
        parsed.append({
            "id": entry_id,
            "event": event,
            "timestamp": timestamp,
            "payload": payload,
        })
    
    return parsed


def summarize_workspace_event(payload: dict[str, Any]) -> str:
    """
    Generates a concise summary of a workspace event.

    Args:
        payload: The event payload dictionary.

    Returns:
        A short summary string.
    """
    if not isinstance(payload, dict) or not payload:
        return ""
    
    for key in ("message", "summary", "description", "command"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    
    if "destination" in payload and "source" in payload:
        return f"{payload['source']} → {payload['destination']}"
    
    items = []
    for key, value in list(payload.items())[:3]:
        if isinstance(value, (str, int, float)):
            items.append(f"{key}={value}")
    
    summary = ", ".join(items)
    if summary:
        return summary
    
    return json.dumps(payload, separators=(",", ":"))[:200]


def get_file_icon(file_path: str) -> str:
    """
    Returns an appropriate icon for a file based on its extension.

    Args:
        file_path: The file path.

    Returns:
        An emoji icon string.
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    
    # Programming languages
    if ext in {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h", ".rs", ".go"}:
        return "📄"
    # Data files
    if ext in {".json", ".yaml", ".yml", ".csv", ".xml"}:
        return "📊"
    # Test files
    if path.name.startswith("test_") or ext == ".test":
        return "📋"
    # Documentation
    if ext in {".md", ".txt", ".rst"}:
        return "📝"
    # Config files
    if ext in {".toml", ".ini", ".conf", ".env"}:
        return "⚙️"
    # Build outputs
    if path.parts and any(part in {"dist", "build", "__pycache__", "node_modules"} for part in path.parts):
        return "📦"
    # Default
    return "📄"



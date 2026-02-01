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
            # Optionally create a README file for initial content
            # This helps users understand the workspace is ready and provides a starting point
            try:
                from datetime import datetime
                created_time = descriptor.get('created', datetime.now().isoformat())
                readme_content = f"""# Workspace: {workspace_id}

This workspace was created on {created_time}.

## Directory Structure

- `/workspace/` - Main workspace directory  
- `/workspace/logs/` - Command execution logs

## Getting Started

You can:
1. Add files to this workspace
2. Execute commands  
3. Create snapshots to track changes
4. Export workspace contents

## Notes

This workspace is isolated and provides a sandboxed environment for agent operations.
"""
                # Use printf instead of echo to handle multiline content properly
                command = f'printf "{readme_content.replace('"', '\\"').replace("\n", "\\n")}" > /workspace/README.md'
                exec_in_workspace(workspace_id, command)
            except Exception:
                # Don't fail workspace creation if README creation fails
                pass
            
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


def get_file_metadata(workspace_id: str, file_path: str) -> dict[str, Any]:
    """
    Retrieves metadata for a file in a workspace.

    Args:
        workspace_id: The workspace ID.
        file_path: The full path to the file inside the workspace.

    Returns:
        A dictionary containing file metadata (size, modified_time, type).
    """
    safe_path = shlex.quote(file_path)
    # Use stat to get file metadata (macOS compatible format)
    command = f"stat -f '%z,%m,%HT' {safe_path} 2>&1 || stat -c '%s,%Y,%F' {safe_path} 2>&1"
    success, stdout, _ = exec_in_workspace(workspace_id, command)
    
    if not success or not stdout:
        return {
            "size": 0,
            "modified_time": "",
            "file_type": "unknown",
        }
    
    parts = stdout.strip().split(",")
    if len(parts) >= 2:
        try:
            size = int(parts[0])
            modified_timestamp = int(parts[1])
            file_type = parts[2] if len(parts) > 2 else "file"
            
            # Convert timestamp to readable format
            from datetime import UTC, datetime
            modified_time = datetime.fromtimestamp(modified_timestamp, tz=UTC).isoformat()
            
            return {
                "size": size,
                "modified_time": modified_time,
                "file_type": file_type,
            }
        except (ValueError, IndexError):
            pass
    
    return {
        "size": 0,
        "modified_time": "",
        "file_type": "unknown",
    }


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


def create_workspace_snapshot(workspace_id: str) -> tuple[bool, dict[str, Any] | None, str | None]:
    """
    Creates a snapshot of all files in a workspace with checksums.
    
    Args:
        workspace_id: The ID of the workspace to snapshot.
    
    Returns:
        A tuple of (success, snapshot_data, error_message).
    """
    from datetime import datetime, timezone
    import hashlib
    
    try:
        # Get all files in the workspace
        files = list_workspace_files(workspace_id)
        
        # Even if no files, create snapshot with empty state (workspace may just have directories)
        # This is valid - a workspace can have just scaffolding directories
        
        # Create snapshot metadata
        snapshot = {
            "workspace_id": workspace_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "files": {},
            "total_files": 0,
            "total_size": 0,
        }
        
        # Compute checksums for each file
        for file_path in files:
            # Skip directories and special files
            if file_path.endswith('/'):
                continue
            
            # Get file content and metadata
            success, content = read_workspace_file(workspace_id, file_path)
            if not success:
                continue  # Skip files we can't read
            
            # Get file metadata
            metadata = get_file_metadata(workspace_id, file_path)
            
            # Compute checksum
            content_bytes = content.encode('utf-8', errors='replace')
            checksum = hashlib.sha256(content_bytes).hexdigest()
            
            # Store file info
            snapshot["files"][file_path] = {
                "checksum": checksum,
                "size": metadata.get("size", len(content_bytes)),
                "modified": metadata.get("modified", ""),
            }
            
            snapshot["total_files"] += 1
            snapshot["total_size"] += metadata.get("size", len(content_bytes))
        
        return True, snapshot, None
        
    except Exception as e:
        return False, None, f"Failed to create snapshot: {str(e)}"


def save_workspace_snapshot(
    client: redis.Redis, 
    workspace_id: str, 
    snapshot: dict[str, Any]
) -> bool:
    """
    Saves a workspace snapshot to Redis.
    
    Args:
        client: Redis client.
        workspace_id: The workspace ID.
        snapshot: The snapshot data.
    
    Returns:
        True if successful, False otherwise.
    """
    try:
        # Generate snapshot ID based on timestamp
        snapshot_id = f"snapshot_{snapshot['timestamp'].replace(':', '').replace('.', '')}"
        
        # Store snapshot in Redis hash
        key = f"qc:workspace:snapshots:{workspace_id}:{snapshot_id}"
        client.hset(key, mapping={"data": json.dumps(snapshot)})
        
        # Also add to a sorted set for ordering by timestamp
        score = float(snapshot['timestamp'].replace('T', '').replace(':', '').replace('-', '').replace('Z', '').replace('.', '')[:14])
        client.zadd(f"qc:workspace:snapshot_list:{workspace_id}", {snapshot_id: score})
        
        return True
    except Exception:
        return False


def load_workspace_snapshots(client: redis.Redis, workspace_id: str) -> list[dict[str, Any]]:
    """
    Loads all snapshots for a workspace.
    
    Args:
        client: Redis client.
        workspace_id: The workspace ID.
    
    Returns:
        List of snapshot data dictionaries, newest first.
    """
    snapshots = []
    
    try:
        # Get snapshot IDs in reverse chronological order
        snapshot_ids = client.zrevrange(f"qc:workspace:snapshot_list:{workspace_id}", 0, -1)
        
        for snapshot_id_bytes in snapshot_ids:
            snapshot_id = snapshot_id_bytes.decode('utf-8') if isinstance(snapshot_id_bytes, bytes) else snapshot_id_bytes
            
            # Load snapshot data
            key = f"qc:workspace:snapshots:{workspace_id}:{snapshot_id}"
            data = client.hget(key, "data")
            if data:
                snapshot_data = json.loads(data)
                snapshot_data["snapshot_id"] = snapshot_id
                snapshots.append(snapshot_data)
    
    except Exception:
        pass  # Return empty list on error
    
    return snapshots


def delete_workspace_snapshot(client: redis.Redis, workspace_id: str, snapshot_id: str) -> bool:
    """
    Deletes a specific snapshot.
    
    Args:
        client: Redis client.
        workspace_id: The workspace ID.
        snapshot_id: The snapshot ID to delete.
    
    Returns:
        True if successful, False otherwise.
    """
    try:
        # Remove from hash
        key = f"qc:workspace:snapshots:{workspace_id}:{snapshot_id}"
        client.delete(key)
        
        # Remove from sorted set
        client.zrem(f"qc:workspace:snapshot_list:{workspace_id}", snapshot_id)
        
        return True
    except Exception:
        return False


def compare_snapshots(
    workspace_id: str,
    snapshot1: dict[str, Any] | None,
    snapshot2: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Compares two snapshots or a snapshot with current workspace state.
    
    Args:
        workspace_id: The workspace ID.
        snapshot1: The first snapshot (or None for current state).
        snapshot2: The second snapshot (or None for current state).
    
    Returns:
        Dictionary with comparison results.
    """
    # Get current state if needed
    if snapshot1 is None:
        success, current, error = create_workspace_snapshot(workspace_id)
        if not success:
            return {"error": error}
        snapshot1 = current
    
    if snapshot2 is None:
        success, current, error = create_workspace_snapshot(workspace_id)
        if not success:
            return {"error": error}
        snapshot2 = current
    
    # Compare files
    files1 = set(snapshot1.get("files", {}).keys())
    files2 = set(snapshot2.get("files", {}).keys())
    
    added = files2 - files1
    deleted = files1 - files2
    common = files1 & files2
    
    modified = []
    for file_path in common:
        checksum1 = snapshot1["files"][file_path].get("checksum")
        checksum2 = snapshot2["files"][file_path].get("checksum")
        if checksum1 != checksum2:
            modified.append(file_path)
    
    return {
        "added": sorted(list(added)),
        "deleted": sorted(list(deleted)),
        "modified": sorted(modified),
        "unchanged": sorted(list(common - set(modified))),
        "summary": {
            "added_count": len(added),
            "deleted_count": len(deleted),
            "modified_count": len(modified),
            "unchanged_count": len(common) - len(modified),
            "total_files_snapshot1": len(files1),
            "total_files_snapshot2": len(files2),
        },
        "timestamp1": snapshot1.get("timestamp"),
        "timestamp2": snapshot2.get("timestamp"),
    }


def get_file_diff(workspace_id: str, file_path: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """
    Gets the diff between a file in a snapshot and its current state.
    
    Args:
        workspace_id: The workspace ID.
        file_path: Path to the file.
        snapshot: The snapshot to compare against.
    
    Returns:
        Dictionary with diff information.
    """
    import difflib
    
    # Get snapshot content (we'd need to store this or reconstruct it)
    snapshot_file = snapshot.get("files", {}).get(file_path)
    if not snapshot_file:
        return {"error": "File not found in snapshot"}
    
    # Get current content
    success, current_content = read_workspace_file(workspace_id, file_path)
    if not success:
        return {"error": f"Could not read current file: {current_content}"}
    
    # For now, we can only show that the file changed (since we don't store content)
    # In a full implementation, we'd store file content or be able to retrieve old versions
    return {
        "file_path": file_path,
        "changed": True,
        "old_checksum": snapshot_file.get("checksum"),
        "old_size": snapshot_file.get("size"),
        "old_modified": snapshot_file.get("modified"),
        "current_content": current_content,
        "note": "Full diff requires storing file content in snapshots",
    }



def ensure_default_workspace(client: redis.Redis) -> bool:
    """
    Ensures that the 'default' workspace is registered in Redis.
    This corresponds to the 'workspace-default' service in docker-compose.
    """
    from quadracode_ui.utils.persistence import save_workspace_descriptor
    
    # Check if already registered
    if client.exists("qc:workspace:descriptor:default"):
        return True
        
    # Attempt to register
    success, descriptor, _ = create_workspace("default")
    if success and descriptor:
        save_workspace_descriptor(client, "default", descriptor)
        return True
        
    return False

"""
This module defines the Pydantic data models and utility functions that serve as 
the shared contract for agent workspaces in the Quadracode system.

Workspaces are isolated environments where agents execute tasks. These contracts 
provide a standardized way to describe the configuration, state, and results of 
operations within these workspaces. This includes descriptors for provisioned 
workspaces, structured results for command execution and file operations, and 
records for workspace snapshots. By centralizing these models, this module 
ensures consistent and reliable interaction with the workspace management system.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, ConfigDict, ValidationInfo, field_validator


DEFAULT_WORKSPACE_MOUNT = "/workspace"
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def normalize_workspace_name(identifier: str) -> str:
    """
    Generates a Docker-safe resource name from a workspace identifier.

    This function sanitizes a given identifier to make it suitable for use as a 
    Docker container or volume name. It replaces spaces, removes unsafe 
    characters, and converts the string to lowercase.

    Args:
        identifier: The raw workspace identifier.

    Returns:
        A sanitized, Docker-safe name.
    """
    slug = identifier.strip()
    if not slug:
        raise ValueError("workspace identifier cannot be empty")
    slug = slug.replace(" ", "-")
    slug = _SAFE_NAME_PATTERN.sub("-", slug)
    slug = slug.strip("-._")
    if not slug:
        raise ValueError("workspace identifier produced empty slug")
    return slug.lower()


class WorkspaceDescriptor(BaseModel):
    """
    Describes a provisioned workspace, including its container and volume.

    This model serves as the canonical representation of a live workspace. It 
    contains all the necessary information to interact with the workspace, such 
    as the container name, the backing volume, and the mount path.
    """

    workspace_id: str = Field(..., description="Stable workspace identifier (usually chat_id).")
    volume: str = Field(..., description="Docker named volume backing the workspace.")
    container: str = Field(..., description="Running workspace container name.")
    mount_path: str = Field(
        default=DEFAULT_WORKSPACE_MOUNT,
        description="Mount point inside the container where the volume is attached.",
    )
    image: str = Field(..., description="Docker image used for the workspace container.")
    created_at: str = Field(..., description="ISO-8601 creation timestamp of the workspace container.")

    model_config = ConfigDict(extra="allow")

    @field_validator("workspace_id", "volume", "container", "image", "created_at")
    @classmethod
    def _require_non_empty(cls, value: str, info: ValidationInfo) -> str:
        """Ensures that key fields are non-empty strings."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value


class WorkspaceCommandResult(BaseModel):
    """
    Provides a structured response for commands executed in a workspace.

    This model captures the complete output of a command, including its exit 
    code, stdout, stderr, and performance metrics. This structured format is 
    essential for the orchestrator and agents to reliably interpret the outcome 
    of their actions.
    """

    workspace: WorkspaceDescriptor
    command: str
    working_dir: str = Field(default=DEFAULT_WORKSPACE_MOUNT)
    environment_keys: List[str] = Field(default_factory=list)
    started_at: str
    finished_at: str
    duration_seconds: float
    returncode: int
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_log_path: Optional[str] = None
    stderr_log_path: Optional[str] = None
    log_bundle_path: Optional[str] = None


class WorkspaceCopyResult(BaseModel):
    """
    Provides a structured response for file copy operations in a workspace.

    This model confirms the details of a copy operation, including the source, 
    destination, and the number of bytes transferred.
    """

    workspace: WorkspaceDescriptor
    source: str
    destination: str
    bytes_transferred: Optional[int] = None


class WorkspaceSnapshotRecord(BaseModel):
    """
    Represents the metadata for a captured workspace snapshot.

    Snapshots are used to preserve the state of a workspace at a critical 
    juncture, such as when an agent encounters an error or reaches a significant 
    milestone. This record contains all the metadata needed to locate and 
    interpret the snapshot artifact.
    """

    snapshot_id: str = Field(..., description="Unique identifier for the snapshot event.")
    workspace_id: str = Field(..., description="Workspace identifier tied to the snapshot.")
    created_at: str = Field(..., description="ISO timestamp when the snapshot was captured.")
    reason: str = Field(..., description="Trigger description (e.g., exhaustion mode, rejection).")
    checksum: str = Field(..., description="Aggregate checksum covering the captured manifest.")
    manifest_path: str = Field(..., description="Filesystem path to the manifest JSON file.")
    archive_path: str = Field(..., description="Filesystem path to the archived workspace tarball.")
    diff_path: Optional[str] = Field(
        default=None,
        description="Optional path to a diff/patch file against the previous snapshot.",
    )
    exhaustion_mode: Optional[str] = Field(
        default=None,
        description="Exhaustion mode (if any) that triggered the snapshot.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata captured at snapshot time (stage, cycle, etc.).",
    )


def collect_environment_keys(env: Optional[dict[str, str]] = None) -> List[str]:
    """
    Extracts and sorts the keys from an environment dictionary.

    This utility is used to get a deterministic list of environment variable 
    keys, which is useful for logging and diagnostics.

    Args:
        env: The environment dictionary.

    Returns:
        A sorted list of environment variable keys.
    """
    if not env:
        return []
    keys: Iterable[str] = env.keys()
    return sorted({str(key) for key in keys})


__all__ = [
    "DEFAULT_WORKSPACE_MOUNT",
    "WorkspaceDescriptor",
    "WorkspaceCommandResult",
    "WorkspaceCopyResult",
    "WorkspaceSnapshotRecord",
    "collect_environment_keys",
    "normalize_workspace_name",
]

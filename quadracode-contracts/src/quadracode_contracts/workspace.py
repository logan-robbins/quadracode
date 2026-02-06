"""
Pydantic data models and utility functions serving as the shared contract for
agent workspaces in the Quadracode system.

Workspaces are isolated environments where agents execute tasks.  These
contracts provide a standardized way to describe the configuration, state, and
results of operations within workspaces.  This includes descriptors for
provisioned workspaces, structured results for command execution and file
operations, and records for workspace snapshots.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .human_clone import HumanCloneExhaustionMode


DEFAULT_WORKSPACE_MOUNT: str = "/workspace"
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def normalize_workspace_name(identifier: str) -> str:
    """Generate a Docker-safe resource name from a workspace identifier.

    Sanitizes a given identifier for use as a Docker container or volume
    name.  Replaces spaces, removes unsafe characters, and lowercases.

    Args:
        identifier: The raw workspace identifier.

    Returns:
        A sanitized, Docker-safe name.

    Raises:
        ValueError: If the identifier is empty or reduces to an empty slug.
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
    """Describes a provisioned workspace, including its container and volume.

    Serves as the canonical representation of a live workspace.  Contains all
    information needed to interact with the workspace — container name,
    backing volume, and mount path.
    """

    model_config = ConfigDict(extra="allow")

    workspace_id: str = Field(..., description="Stable workspace identifier (usually chat_id).")
    volume: str = Field(..., description="Docker named volume backing the workspace.")
    container: str = Field(..., description="Running workspace container name.")
    mount_path: str = Field(
        default=DEFAULT_WORKSPACE_MOUNT,
        description="Mount point inside the container where the volume is attached.",
    )
    image: str = Field(..., description="Docker image used for the workspace container.")
    created_at: str = Field(..., description="ISO-8601 creation timestamp of the workspace container.")

    @field_validator("workspace_id", "volume", "container", "image", "created_at")
    @classmethod
    def _require_non_empty(cls, value: str, info: ValidationInfo) -> str:
        """Ensure key fields are non-empty strings."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value


class WorkspaceCommandResult(BaseModel):
    """Structured response for commands executed in a workspace.

    Captures the complete output of a command — exit code, stdout, stderr,
    and performance metrics.  This structured format allows the orchestrator
    and agents to reliably interpret command outcomes.
    """

    workspace: WorkspaceDescriptor
    command: str
    working_dir: str = Field(default=DEFAULT_WORKSPACE_MOUNT)
    environment_keys: list[str] = Field(default_factory=list)
    started_at: str
    finished_at: str
    duration_seconds: float = Field(..., ge=0.0)
    returncode: int
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = Field(default=0, ge=0)
    stderr_bytes: int = Field(default=0, ge=0)
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    log_bundle_path: str | None = None


class WorkspaceCopyResult(BaseModel):
    """Structured response for file copy operations in a workspace.

    Confirms the details of a copy operation — source, destination, and the
    number of bytes transferred.
    """

    workspace: WorkspaceDescriptor
    source: str
    destination: str
    bytes_transferred: int | None = Field(default=None, ge=0)


class WorkspaceSnapshotRecord(BaseModel):
    """Metadata for a captured workspace snapshot.

    Snapshots preserve workspace state at critical junctures (e.g., agent
    error, significant milestone).  This record contains all metadata needed
    to locate and interpret the snapshot artifact.
    """

    snapshot_id: str = Field(..., description="Unique identifier for the snapshot event.")
    workspace_id: str = Field(..., description="Workspace identifier tied to the snapshot.")
    created_at: str = Field(..., description="ISO timestamp when the snapshot was captured.")
    reason: str = Field(..., description="Trigger description (e.g., exhaustion mode, rejection).")
    checksum: str = Field(..., description="Aggregate checksum covering the captured manifest.")
    manifest_path: str = Field(..., description="Filesystem path to the manifest JSON file.")
    archive_path: str = Field(..., description="Filesystem path to the archived workspace tarball.")
    diff_path: str | None = Field(
        default=None,
        description="Optional path to a diff/patch file against the previous snapshot.",
    )
    exhaustion_mode: HumanCloneExhaustionMode | None = Field(
        default=None,
        description="Exhaustion mode (if any) that triggered the snapshot.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata captured at snapshot time (stage, cycle, etc.).",
    )


def collect_environment_keys(env: dict[str, str] | None = None) -> list[str]:
    """Extract and sort the keys from an environment dictionary.

    Produces a deterministic list of environment variable keys, useful for
    logging and diagnostics.

    Args:
        env: The environment dictionary.

    Returns:
        A sorted list of environment variable keys.
    """
    if not env:
        return []
    return sorted({str(key) for key in env})


__all__ = [
    "DEFAULT_WORKSPACE_MOUNT",
    "WorkspaceDescriptor",
    "WorkspaceCommandResult",
    "WorkspaceCopyResult",
    "WorkspaceSnapshotRecord",
    "collect_environment_keys",
    "normalize_workspace_name",
]

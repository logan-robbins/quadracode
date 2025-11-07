"""Workspace-related contract models."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, ConfigDict, ValidationInfo, field_validator


DEFAULT_WORKSPACE_MOUNT = "/workspace"
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def normalize_workspace_name(identifier: str) -> str:
    """Generate a Docker-safe resource suffix from a workspace identifier."""
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
    """Descriptor describing a provisioned workspace container/volume pair."""

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
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value


class WorkspaceCommandResult(BaseModel):
    """Structured response describing a command executed inside a workspace."""

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
    """Structured response for copy operations."""

    workspace: WorkspaceDescriptor
    source: str
    destination: str
    bytes_transferred: Optional[int] = None


class WorkspaceSnapshotRecord(BaseModel):
    """Metadata describing a captured workspace snapshot artifact."""

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
    """Utility to extract environment variable keys in deterministic order."""
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

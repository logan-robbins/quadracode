"""Tests for workspace module."""
import pytest
from pydantic import ValidationError

from quadracode_contracts.workspace import (
    DEFAULT_WORKSPACE_MOUNT,
    normalize_workspace_name,
    WorkspaceDescriptor,
    WorkspaceCommandResult,
    WorkspaceCopyResult,
    WorkspaceSnapshotRecord,
    collect_environment_keys,
)


class TestConstants:
    """Tests for workspace constants."""

    def test_default_mount_path(self):
        """Should have expected default mount path."""
        assert DEFAULT_WORKSPACE_MOUNT == "/workspace"


class TestNormalizeWorkspaceName:
    """Tests for normalize_workspace_name function."""

    def test_simple_name(self):
        """Should pass through simple valid names."""
        assert normalize_workspace_name("myworkspace") == "myworkspace"

    def test_converts_to_lowercase(self):
        """Should convert to lowercase."""
        assert normalize_workspace_name("MyWorkSpace") == "myworkspace"

    def test_replaces_spaces_with_dashes(self):
        """Should replace spaces with dashes."""
        assert normalize_workspace_name("my workspace name") == "my-workspace-name"

    def test_removes_unsafe_characters(self):
        """Should remove characters unsafe for Docker."""
        assert normalize_workspace_name("test@workspace!") == "test-workspace"
        assert normalize_workspace_name("test:workspace") == "test-workspace"

    def test_strips_leading_trailing(self):
        """Should strip leading/trailing whitespace and special chars."""
        assert normalize_workspace_name("  myworkspace  ") == "myworkspace"
        assert normalize_workspace_name("-myworkspace-") == "myworkspace"

    def test_empty_string_raises(self):
        """Should raise for empty string."""
        with pytest.raises(ValueError, match="cannot be empty"):
            normalize_workspace_name("")

    def test_whitespace_only_raises(self):
        """Should raise for whitespace-only string."""
        with pytest.raises(ValueError, match="cannot be empty"):
            normalize_workspace_name("   ")

    def test_special_chars_only_raises(self):
        """Should raise if all chars are stripped."""
        with pytest.raises(ValueError, match="produced empty slug"):
            normalize_workspace_name("@#$%^&*()")

    def test_realistic_chat_id(self):
        """Should handle realistic chat ID format."""
        result = normalize_workspace_name("chat-2024-01-15-abc123")
        assert result == "chat-2024-01-15-abc123"


class TestWorkspaceDescriptor:
    """Tests for WorkspaceDescriptor model."""

    def test_valid_descriptor(self):
        """Should create valid descriptor."""
        descriptor = WorkspaceDescriptor(
            workspace_id="chat-prod-001",
            volume="qc-vol-chat-prod-001",
            container="qc-ws-chat-prod-001",
            image="quadracode-workspace:latest",
            created_at="2024-01-15T10:30:00Z"
        )
        assert descriptor.workspace_id == "chat-prod-001"
        assert descriptor.mount_path == DEFAULT_WORKSPACE_MOUNT

    def test_custom_mount_path(self):
        """Should accept custom mount path."""
        descriptor = WorkspaceDescriptor(
            workspace_id="custom-ws",
            volume="custom-vol",
            container="custom-container",
            mount_path="/custom/mount",
            image="custom-image:v1",
            created_at="2024-01-15T10:30:00Z"
        )
        assert descriptor.mount_path == "/custom/mount"

    def test_extra_fields_allowed(self):
        """Should allow extra fields."""
        descriptor = WorkspaceDescriptor(
            workspace_id="test-ws",
            volume="test-vol",
            container="test-container",
            image="test-image",
            created_at="2024-01-15T10:30:00Z",
            custom_field="extra data",
            another_field=123
        )
        assert descriptor.custom_field == "extra data"

    def test_empty_workspace_id_rejected(self):
        """Should reject empty workspace_id."""
        with pytest.raises(ValidationError):
            WorkspaceDescriptor(
                workspace_id="",
                volume="vol",
                container="container",
                image="image",
                created_at="2024-01-15T10:30:00Z"
            )

    def test_whitespace_only_rejected(self):
        """Should reject whitespace-only required fields."""
        with pytest.raises(ValidationError):
            WorkspaceDescriptor(
                workspace_id="   ",
                volume="vol",
                container="container",
                image="image",
                created_at="2024-01-15T10:30:00Z"
            )


class TestWorkspaceCommandResult:
    """Tests for WorkspaceCommandResult model."""

    def test_successful_command(self):
        """Should capture successful command execution."""
        workspace = WorkspaceDescriptor(
            workspace_id="cmd-test",
            volume="cmd-vol",
            container="cmd-container",
            image="quadracode-workspace:latest",
            created_at="2024-01-15T10:30:00Z"
        )
        result = WorkspaceCommandResult(
            workspace=workspace,
            command="pytest tests/ -v",
            started_at="2024-01-15T10:30:00Z",
            finished_at="2024-01-15T10:30:05Z",
            duration_seconds=5.123,
            returncode=0,
            stdout="10 passed in 5.12s",
            stderr=""
        )
        assert result.returncode == 0
        assert result.duration_seconds == 5.123

    def test_failed_command(self):
        """Should capture failed command execution."""
        workspace = WorkspaceDescriptor(
            workspace_id="fail-test",
            volume="fail-vol",
            container="fail-container",
            image="quadracode-workspace:latest",
            created_at="2024-01-15T10:30:00Z"
        )
        result = WorkspaceCommandResult(
            workspace=workspace,
            command="pip install nonexistent-package-xyz",
            started_at="2024-01-15T10:30:00Z",
            finished_at="2024-01-15T10:30:02Z",
            duration_seconds=2.5,
            returncode=1,
            stdout="",
            stderr="ERROR: No matching distribution found"
        )
        assert result.returncode == 1
        assert "No matching distribution" in result.stderr

    def test_with_log_paths(self):
        """Should accept optional log paths."""
        workspace = WorkspaceDescriptor(
            workspace_id="log-test",
            volume="log-vol",
            container="log-container",
            image="quadracode-workspace:latest",
            created_at="2024-01-15T10:30:00Z"
        )
        result = WorkspaceCommandResult(
            workspace=workspace,
            command="make build",
            started_at="2024-01-15T10:30:00Z",
            finished_at="2024-01-15T10:35:00Z",
            duration_seconds=300.0,
            returncode=0,
            stdout_log_path="/logs/stdout.log",
            stderr_log_path="/logs/stderr.log",
            log_bundle_path="/logs/bundle.tar.gz"
        )
        assert result.stdout_log_path == "/logs/stdout.log"


class TestWorkspaceCopyResult:
    """Tests for WorkspaceCopyResult model."""

    def test_copy_result(self):
        """Should capture copy operation result."""
        workspace = WorkspaceDescriptor(
            workspace_id="copy-test",
            volume="copy-vol",
            container="copy-container",
            image="quadracode-workspace:latest",
            created_at="2024-01-15T10:30:00Z"
        )
        result = WorkspaceCopyResult(
            workspace=workspace,
            source="/local/data.csv",
            destination="/workspace/data.csv",
            bytes_transferred=1048576  # 1MB
        )
        assert result.bytes_transferred == 1048576

    def test_copy_without_bytes(self):
        """bytes_transferred should be optional."""
        workspace = WorkspaceDescriptor(
            workspace_id="copy-test",
            volume="copy-vol",
            container="copy-container",
            image="quadracode-workspace:latest",
            created_at="2024-01-15T10:30:00Z"
        )
        result = WorkspaceCopyResult(
            workspace=workspace,
            source="/src",
            destination="/dst"
        )
        assert result.bytes_transferred is None


class TestWorkspaceSnapshotRecord:
    """Tests for WorkspaceSnapshotRecord model."""

    def test_valid_snapshot(self):
        """Should create valid snapshot record."""
        snapshot = WorkspaceSnapshotRecord(
            snapshot_id="snap-2024-001",
            workspace_id="chat-prod-001",
            created_at="2024-01-15T10:30:00Z",
            reason="HumanClone rejection - test failure",
            checksum="sha256:abc123def456",
            manifest_path="/snapshots/snap-2024-001/manifest.json",
            archive_path="/snapshots/snap-2024-001/workspace.tar.gz"
        )
        assert snapshot.snapshot_id == "snap-2024-001"
        assert snapshot.diff_path is None  # optional

    def test_snapshot_with_all_fields(self):
        """Should accept all optional fields."""
        snapshot = WorkspaceSnapshotRecord(
            snapshot_id="snap-2024-002",
            workspace_id="chat-prod-002",
            created_at="2024-01-15T11:00:00Z",
            reason="Scheduled checkpoint",
            checksum="sha256:xyz789",
            manifest_path="/snapshots/snap-2024-002/manifest.json",
            archive_path="/snapshots/snap-2024-002/workspace.tar.gz",
            diff_path="/snapshots/snap-2024-002/diff.patch",
            exhaustion_mode="context_saturation",
            metadata={
                "cycle": 15,
                "stage": "validation",
                "file_count": 42
            }
        )
        assert snapshot.diff_path is not None
        assert snapshot.exhaustion_mode == "context_saturation"
        assert snapshot.metadata["file_count"] == 42


class TestCollectEnvironmentKeys:
    """Tests for collect_environment_keys function."""

    def test_empty_env(self):
        """Should return empty list for empty/None env."""
        assert collect_environment_keys(None) == []
        assert collect_environment_keys({}) == []

    def test_sorts_keys(self):
        """Should return sorted keys."""
        env = {
            "ZEBRA": "value",
            "ALPHA": "value",
            "MIDDLE": "value"
        }
        keys = collect_environment_keys(env)
        assert keys == ["ALPHA", "MIDDLE", "ZEBRA"]

    def test_deduplicates_keys(self):
        """Should handle dict (inherently unique keys)."""
        env = {
            "KEY1": "value1",
            "KEY2": "value2"
        }
        keys = collect_environment_keys(env)
        assert len(keys) == 2

    def test_realistic_env(self):
        """Should handle realistic environment variables."""
        env = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "ANTHROPIC_API_KEY": "sk-***",
            "REDIS_HOST": "localhost",
            "REDIS_PORT": "6379"
        }
        keys = collect_environment_keys(env)
        assert "ANTHROPIC_API_KEY" in keys
        assert "REDIS_HOST" in keys
        assert keys == sorted(keys)  # verify sorted

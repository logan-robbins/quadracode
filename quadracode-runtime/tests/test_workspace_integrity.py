from __future__ import annotations

import json
from pathlib import Path

from quadracode_runtime.state import make_initial_context_engine_state
from quadracode_runtime.workspace_integrity import (
    capture_workspace_snapshot,
    validate_workspace_integrity,
)


def _make_state(workspace_path: Path) -> dict:
    state = make_initial_context_engine_state()
    state["workspace"] = {
        "workspace_id": "test-workspace",
        "host_path": str(workspace_path),
    }
    return state


def test_capture_workspace_snapshot_records_manifest_and_diff(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("alpha", encoding="utf-8")

    state = _make_state(workspace)

    first_snapshot = capture_workspace_snapshot(
        state,
        reason="unit-test",
        stage="initial",
    )
    assert first_snapshot is not None
    manifest_entries = json.loads(Path(first_snapshot.manifest_path).read_text(encoding="utf-8"))
    assert any(entry["path"].endswith("alpha.txt") for entry in manifest_entries)
    assert first_snapshot.diff_path is None

    # mutate workspace to ensure diff is generated
    (workspace / "alpha.txt").write_text("beta", encoding="utf-8")
    (workspace / "beta.txt").write_text("new", encoding="utf-8")

    second_snapshot = capture_workspace_snapshot(
        state,
        reason="unit-test",
        stage="mutation",
    )
    assert second_snapshot is not None
    assert second_snapshot.diff_path is not None
    diff_text = Path(second_snapshot.diff_path).read_text(encoding="utf-8")
    assert "alpha.txt" in diff_text
    assert "beta.txt" in diff_text


def test_validate_and_restore_workspace_detects_drift(tmp_path) -> None:
    workspace = tmp_path / "ws-validate"
    workspace.mkdir()
    target_file = workspace / "notes.md"
    target_file.write_text("v1", encoding="utf-8")

    state = _make_state(workspace)
    capture_workspace_snapshot(state, reason="baseline", stage="setup")

    # introduce drift
    target_file.write_text("v2", encoding="utf-8")
    extra_file = workspace / "temp.txt"
    extra_file.write_text("extra", encoding="utf-8")

    result = validate_workspace_integrity(state, reason="drift", auto_restore=True)
    assert result is not None
    assert not result.valid
    assert result.restored
    assert target_file.read_text(encoding="utf-8") == "v1"
    assert not extra_file.exists()

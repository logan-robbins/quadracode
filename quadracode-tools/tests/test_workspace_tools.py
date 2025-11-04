from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from quadracode_tools.tools.workspace import ensure_workspace, workspace_create

@patch("quadracode_tools.tools.workspace._publish_workspace_event")
@patch("quadracode_tools.tools.workspace._workspace_descriptor")
@patch("quadracode_tools.tools.workspace._start_container")
@patch("quadracode_tools.tools.workspace._inspect_container")
@patch("quadracode_tools.tools.workspace._container_running")
@patch("quadracode_tools.tools.workspace._container_exists")
@patch("quadracode_tools.tools.workspace._volume_exists")
def test_ensure_workspace_creates_new_environment(
    mock_volume_exists: MagicMock,
    mock_container_exists: MagicMock,
    mock_container_running: MagicMock,
    mock_inspect_container: MagicMock,
    mock_start_container: MagicMock,
    mock_workspace_descriptor: MagicMock,
    mock_publish_event: MagicMock,
) -> None:
    mock_volume_exists.return_value = False
    mock_container_exists.return_value = False
    mock_container_running.return_value = False
    mock_inspect_container.return_value = {"Id": "abc", "Config": {"Image": "quadracode-workspace:latest"}}
    fake_descriptor = MagicMock()
    mock_workspace_descriptor.return_value = fake_descriptor

    with patch("quadracode_tools.tools.workspace._run_docker") as mock_run_docker:
        mock_run_docker.return_value = MagicMock(returncode=0, stdout="")
        success, result_descriptor, error = ensure_workspace("chat-123")

    assert success
    assert error is None
    assert result_descriptor is fake_descriptor
    mock_volume_exists.assert_called_once()
    mock_container_exists.assert_called_once()
    mock_start_container.assert_called_once()
    mock_publish_event.assert_called_once()
    published_args = mock_publish_event.call_args.args
    assert published_args[0] == "chat-123"
    assert published_args[1] == "workspace_created"


@patch("quadracode_tools.tools.workspace._publish_workspace_event")
def test_workspace_create_returns_error_on_failure(
    mock_publish_event: MagicMock,
) -> None:
    with patch(
        "quadracode_tools.tools.workspace.ensure_workspace",
        return_value=(False, None, "failure reason"),
    ):
        result = workspace_create.invoke({"workspace_id": "chat-err"})
    payload = json.loads(result)

    assert not payload["success"]
    assert payload["error"] == "failure reason"
    assert mock_publish_event.call_count == 0


@patch("quadracode_tools.tools.workspace._publish_workspace_event")
@patch("quadracode_tools.tools.workspace._workspace_descriptor")
@patch("quadracode_tools.tools.workspace._start_container")
@patch("quadracode_tools.tools.workspace._inspect_container")
@patch("quadracode_tools.tools.workspace._container_running")
@patch("quadracode_tools.tools.workspace._container_exists")
@patch("quadracode_tools.tools.workspace._volume_exists")
def test_ensure_workspace_handles_existing_stopped_container(
    mock_volume_exists: MagicMock,
    mock_container_exists: MagicMock,
    mock_container_running: MagicMock,
    mock_inspect_container: MagicMock,
    mock_start_container: MagicMock,
    mock_workspace_descriptor: MagicMock,
    mock_publish_event: MagicMock,
) -> None:
    mock_volume_exists.return_value = True
    mock_container_exists.return_value = True
    mock_container_running.side_effect = [False, True]
    mock_inspect_container.return_value = {"Id": "container-id", "Config": {"Image": "quadracode-workspace:latest"}}
    fake_descriptor = MagicMock()
    mock_workspace_descriptor.return_value = fake_descriptor

    with patch("quadracode_tools.tools.workspace._run_docker") as mock_run_docker:
        mock_run_docker.side_effect = [
            MagicMock(returncode=0, stdout=""),
        ]
        success, descriptor, error = ensure_workspace("chat-existing")

    assert success
    assert error is None
    assert descriptor is not None
    mock_start_container.assert_not_called()
    mock_publish_event.assert_called_once()
    event_name = mock_publish_event.call_args.args[1]
    assert event_name == "workspace_started"


@patch("quadracode_tools.tools.workspace._publish_workspace_event")
@patch("quadracode_tools.tools.workspace._workspace_descriptor")
@patch("quadracode_tools.tools.workspace._start_container")
@patch("quadracode_tools.tools.workspace._inspect_container")
@patch("quadracode_tools.tools.workspace._container_running")
@patch("quadracode_tools.tools.workspace._container_exists")
@patch("quadracode_tools.tools.workspace._volume_exists")
def test_ensure_workspace_returns_error_when_docker_fails(
    mock_volume_exists: MagicMock,
    mock_container_exists: MagicMock,
    mock_container_running: MagicMock,
    mock_inspect_container: MagicMock,
    mock_start_container: MagicMock,
    mock_workspace_descriptor: MagicMock,
    mock_publish_event: MagicMock,
) -> None:
    mock_volume_exists.return_value = False
    mock_container_exists.return_value = False
    mock_container_running.return_value = False
    mock_workspace_descriptor.return_value = MagicMock()

    failure_process = MagicMock()
    failure_process.returncode = 1
    failure_process.stderr = "boom"

    with patch("quadracode_tools.tools.workspace._run_docker") as mock_run_docker:
        mock_run_docker.side_effect = [failure_process]
        success, descriptor, error = ensure_workspace("chat-fail")

    assert not success
    assert descriptor is None
    assert error is not None
    mock_publish_event.assert_not_called()

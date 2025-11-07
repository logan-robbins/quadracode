from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from quadracode_tools.tools.test_suite import (
    DiscoveredTestCommand,
    discover_test_commands,
    execute_full_test_suite,
)


def test_discover_test_commands_detects_pyproject_and_makefile(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'dummy'\n")
    (tmp_path / "Makefile").write_text("test:\n\t@echo running tests\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "pytest"}}))

    sub_pkg = tmp_path / "quadracode-runtime"
    sub_pkg.mkdir()
    (sub_pkg / "pyproject.toml").write_text("[project]\nname = 'sub'\n")

    commands = discover_test_commands(tmp_path)

    command_descriptions = sorted(spec.description for spec in commands)
    assert "pyproject:root" in command_descriptions
    assert "pyproject:quadracode-runtime" in command_descriptions
    assert "make:test" in command_descriptions
    assert "package.json:test" in command_descriptions


@patch("quadracode_tools.tools.test_suite.discover_test_commands")
@patch("quadracode_tools.tools.test_suite._spawn_debugger_agent")
@patch("quadracode_tools.tools.test_suite.subprocess.run")
def test_execute_full_test_suite_spawns_debugger_on_failure(
    mock_run: MagicMock,
    mock_spawn: MagicMock,
    mock_discover: MagicMock,
    tmp_path: Path,
) -> None:
    mock_discover.return_value = [
        DiscoveredTestCommand(
            command=("python", "-m", "pytest"),
            cwd=tmp_path,
            description="pyproject:root",
        )
    ]
    mock_process = MagicMock()
    mock_process.stdout = "FAILED tests/test_app.py::test_feature"
    mock_process.stderr = "AssertionError"
    mock_process.returncode = 1
    mock_run.return_value = mock_process
    mock_spawn.return_value = {"action": "spawn_debugger_agent", "agent_id": "debugger-abc"}

    result = execute_full_test_suite(workspace_root=str(tmp_path))

    assert result["overall_status"] == "failed"
    assert result["summary"]["fail_count"] == 1
    mock_spawn.assert_called_once()
    assert "remediation" in result
    assert result["remediation"]["agent_id"] == "debugger-abc"

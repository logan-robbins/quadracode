"""Provides a LangChain tool for discovering and executing a comprehensive test suite.

This module equips an agent with the ability to perform robust, automated quality
assurance checks on a codebase. The `run_full_test_suite` tool discovers test
commands from repository metadata (e.g., `pyproject.toml`, `package.json`,
`Makefile`), executes them in isolated subprocesses, and captures structured
results. It records pass/fail status, timings, output streams, and code coverage.
In the event of test failures, it can autonomously spawn a specialized debugger
agent to diagnose the root cause, creating a closed loop of test execution and
remediation.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from quadracode_contracts import DEFAULT_WORKSPACE_MOUNT

from .agent_management import _run_script


@dataclass(frozen=True)
class DiscoveredTestCommand:
    """Canonical test command specification discovered from the workspace.

    Represents a single, executable test command found by scanning the repository's
    metadata files. It includes the command arguments, the working directory, and a
    description of its origin (e.g., 'pyproject:root').
    """

    command: Tuple[str, ...]
    cwd: Path
    description: str
    environment: Dict[str, str] | None = None


class RunFullTestSuiteRequest(BaseModel):
    """Input payload for running the full automated test battery.

    This schema defines the configurable parameters for the test suite execution tool,
    allowing an agent to control aspects like the target workspace, inclusion of
    end-to-end tests, and execution timeouts.
    """

    workspace_root: Optional[str] = Field(
        default=None,
        description="Path to the workspace root. Defaults to current working directory.",
    )
    include_e2e: bool = Field(
        default=True,
        description="Include expensive end-to-end suites if discovery finds them.",
    )
    timeout_seconds: int = Field(
        default=1800,
        ge=60,
        description="Per-command timeout to avoid wedging the orchestrator.",
    )
    max_output_chars: int = Field(
        default=6000,
        ge=500,
        description="Limit captured stdout/stderr to this many characters per stream.",
    )


def discover_test_commands(root: Path, *, include_e2e: bool = True) -> List[DiscoveredTestCommand]:
    """Infers the set of test commands worth running from workspace metadata.

    Scans a given workspace directory for common project configuration files that
    indicate the presence of a test suite. It supports:
    - `pyproject.toml` (for `uv run pytest`) at the root and in subdirectories.
    - `package.json` (for `npm run test`).
    - `Makefile` (for `make test`).
    - A `tests/` directory for end-to-end tests.

    Returns a list of `DiscoveredTestCommand` objects, avoiding duplicates.
    """

    commands: List[DiscoveredTestCommand] = []
    seen: set[Tuple[Tuple[str, ...], Path]] = set()

    def _register(command: Sequence[str], cwd: Path, description: str) -> None:
        key = (tuple(command), cwd)
        if key in seen:
            return
        seen.add(key)
        commands.append(
            DiscoveredTestCommand(
                command=tuple(command),
                cwd=cwd,
                description=description,
            )
        )

    def _has_make_target(makefile: Path, target: str) -> bool:
        try:
            content = makefile.read_text()
        except Exception:
            return False
        pattern = re.compile(rf"^\s*{re.escape(target)}\s*:", re.MULTILINE)
        return bool(pattern.search(content))

    if (root / "pyproject.toml").exists():
        _register(("uv", "run", "pytest"), root, "pyproject:root")

    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        child_pyproject = child / "pyproject.toml"
        if child_pyproject.exists():
            _register(("uv", "run", "pytest"), child, f"pyproject:{child.name}")

    package_json = root / "package.json"
    if package_json.exists():
        try:
            package_payload = json.loads(package_json.read_text())
            scripts = package_payload.get("scripts", {})
            script_value = scripts.get("test")
            if isinstance(script_value, str) and "no test specified" not in script_value.lower():
                _register(("npm", "run", "test"), root, "package.json:test")
        except Exception:
            pass

    makefile = root / "Makefile"
    if makefile.exists() and _has_make_target(makefile, "test"):
        _register(("make", "test"), root, "make:test")

    e2e_dir = root / "tests" / "e2e"
    if include_e2e and e2e_dir.exists():
        _register(("uv", "run", "pytest", "tests/", "-m", "e2e"), root, "pytest:e2e")

    return commands


def execute_full_test_suite(
    *,
    workspace_root: str | None = None,
    include_e2e: bool = True,
    timeout_seconds: int = 1800,
    max_output_chars: int = 6000,
) -> Dict[str, Any]:
    """Runs discovered test commands, capturing structured results and telemetry.

    This is the core implementation behind the `run_full_test_suite` tool. It
    first calls `discover_test_commands` to identify what to run. It then iterates
    through each command, executing it in a subprocess with a configurable timeout.
    It captures stdout, stderr, and the return code, and parses output for code
    coverage metrics. The results of each command are aggregated into a comprehensive
    JSON report that summarizes the overall status of the test suite. If failures
or timed_out, the tool will try to spawn a debugger agent.
    """

    root_path = Path(workspace_root or os.environ.get("WORKSPACE_ROOT") or os.getcwd()).resolve()
    commands = discover_test_commands(root_path, include_e2e=include_e2e)
    started_at = datetime.now(timezone.utc)
    command_results: List[Dict[str, Any]] = []
    pass_count = 0
    fail_count = 0
    coverage_values: List[float] = []

    for spec in commands:
        command_env = os.environ.copy()
        if spec.environment:
            command_env.update(spec.environment)
        start = time.perf_counter()
        stdout = ""
        stderr = ""
        returncode = -1
        timed_out = False
        try:
            completed = subprocess.run(  # noqa: S603
                list(spec.command),
                cwd=str(spec.cwd),
                capture_output=True,
                text=True,
                env=command_env,
                timeout=timeout_seconds,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - rare
            timed_out = True
            stdout = (exc.stdout or "") + "\n[quadracode] command timed out"
            stderr = exc.stderr or ""
        except FileNotFoundError as exc:  # pragma: no cover - environment issue
            stderr = str(exc)
        duration = time.perf_counter() - start

        status = "passed" if returncode == 0 and not timed_out else "failed"
        pass_count += 1 if status == "passed" else 0
        fail_count += 1 if status == "failed" else 0
        coverage = _extract_coverage(stdout, stderr)
        if coverage is not None:
            coverage_values.append(coverage)

        command_results.append(
            {
                "command": " ".join(spec.command),
                "cwd": str(spec.cwd),
                "description": spec.description,
                "status": status,
                "returncode": returncode,
                "duration_seconds": round(duration, 3),
                "stdout": _truncate_output(stdout, max_output_chars),
                "stderr": _truncate_output(stderr, max_output_chars),
                "coverage_percent": coverage,
            }
        )

    completed_at = datetime.now(timezone.utc)
    overall_status = "skipped"
    if command_results:
        overall_status = "passed" if fail_count == 0 else "failed"

    coverage_summary: Dict[str, float] | None = None
    if coverage_values:
        coverage_summary = {
            "min": min(coverage_values),
            "max": max(coverage_values),
            "avg": sum(coverage_values) / len(coverage_values),
        }

    response: Dict[str, Any] = {
        "tool": "run_full_test_suite",
        "workspace_root": str(root_path),
        "overall_status": overall_status,
        "started_at": started_at.isoformat(timespec="seconds"),
        "completed_at": completed_at.isoformat(timespec="seconds"),
        "summary": {
            "commands_discovered": len(commands),
            "commands_executed": len(command_results),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "include_e2e": include_e2e,
        },
        "coverage": coverage_summary,
        "commands": command_results,
    }

    if overall_status == "failed":
        response["remediation"] = _spawn_debugger_agent(root_path)
    elif overall_status == "skipped":
        response["remediation"] = {
            "action": "noop",
            "reason": "No test commands discovered",
        }

    return response


def _truncate_output(value: str, limit: int) -> str:
    """Truncates a string to a maximum length, preserving the end of the string."""
    if not value:
        return ""
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def _extract_coverage(stdout: str, stderr: str) -> Optional[float]:
    """Parses stdout/stderr for common code coverage report formats.

    Uses a series of regular expressions to find coverage percentages in the text
    output of test runners like Pytest's `coverage` plugin. Returns the first
    matched percentage as a float.
    """
    combined = f"{stdout}\n{stderr}"
    patterns = [
        re.compile(r"TOTAL\s+\d+\s+\d+\s+\d+\s+\d+\s+(?P<pct>\d+)%"),
        re.compile(r"(?i)coverage[:\s]+(?P<pct>\d+)%"),
        re.compile(r"(?i)lines\s*:\s*(?P<pct>\d+)%"),
    ]
    for pattern in patterns:
        match = pattern.search(combined)
        if match:
            try:
                return float(match.group("pct"))
            except ValueError:
                continue
    return None


def _spawn_debugger_agent(root: Path) -> Dict[str, Any]:
    """Spawns a new debugger agent in response to test failures.

    When the test suite fails, this function is called to initiate an automated
    debugging process. It constructs the necessary environment variables and calls
    an underlying `spawn-agent.sh` script to launch a new agent container. This
    new agent is configured with access to the same workspace, allowing it to
    introspect the code, analyze the failure, and potentially propose a fix.
    """
    descriptor = _active_workspace_descriptor()
    workspace_mount = DEFAULT_WORKSPACE_MOUNT
    env_overrides: Dict[str, str] = {}
    if descriptor:
        workspace_id = descriptor.get("workspace_id")
        workspace_volume = descriptor.get("volume")
        workspace_mount = descriptor.get("mount_path") or workspace_mount
        if workspace_id:
            env_overrides["QUADRACODE_WORKSPACE_ID"] = str(workspace_id)
        if workspace_volume:
            env_overrides["QUADRACODE_WORKSPACE_VOLUME"] = str(workspace_volume)
            env_overrides["QUADRACODE_WORKSPACE_MOUNT"] = workspace_mount

    agent_suffix = secrets.token_hex(3)
    workspace_label = descriptor.get("workspace_id") if descriptor else root.name
    agent_id = f"debugger-{workspace_label}-{agent_suffix}"
    image = os.environ.get("QUADRACODE_DEBUGGER_AGENT_IMAGE", "quadracode-agent")
    network = os.environ.get("QUADRACODE_DEBUGGER_AGENT_NETWORK", "quadracode_default")
    response = _run_script(
        "spawn-agent.sh",
        agent_id,
        image,
        network,
        env_overrides=env_overrides or None,
    )
    success = bool(response.get("success"))
    payload = {
        "action": "spawn_debugger_agent",
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agent_id": response.get("agent_id", agent_id),
        "workspace": descriptor or {"root": str(root)},
        "success": success,
        "response": response,
    }
    if not success:
        payload["error"] = response.get("error") or response.get("message")
    return payload


def _active_workspace_descriptor() -> Dict[str, Any] | None:
    """Retrieves the active workspace descriptor from environment variables.

    The workspace descriptor is a JSON string containing metadata about the current
    execution environment, such as the workspace ID and volume information. This
    is used to ensure that the debugger agent is spawned with the correct context.
    """
    descriptor_raw = os.environ.get("QUADRACODE_ACTIVE_WORKSPACE_DESCRIPTOR")
    if not descriptor_raw:
        return None
    try:
        payload = json.loads(descriptor_raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


@tool(args_schema=RunFullTestSuiteRequest)
def run_full_test_suite(
    workspace_root: str | None = None,
    include_e2e: bool = True,
    timeout_seconds: int = 1800,
    max_output_chars: int = 6000,
) -> str:
    """Discovers and executes all relevant unit and end-to-end tests in the workspace.

    This tool provides a high-level action for an agent to validate the correctness
    of a codebase. It automatically finds test commands based on the project's
    structure and runs them, collecting detailed results.

    Key features:
    - **Auto-discovery**: Finds tests in `pyproject.toml`, `package.json`, and `Makefile`.
    - **Comprehensive Execution**: Runs all discovered test suites, including optional e2e tests.
    - **Structured Telemetry**: Returns a JSON object with detailed results, timings,
      and coverage information.
    - **Automated Remediation**: If tests fail, it can trigger the spawning of a
      specialized debugger agent to investigate the failures.
    """

    result = execute_full_test_suite(
        workspace_root=workspace_root,
        include_e2e=include_e2e,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )
    return json.dumps(result, indent=2, sort_keys=True)


# Stable tool naming for LangGraph registrations
run_full_test_suite.name = "run_full_test_suite"

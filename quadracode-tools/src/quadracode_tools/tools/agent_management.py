"""Provides a LangChain tool for dynamic, runtime management of agent lifecycles.

This module allows a privileged agent (typically an orchestrator) to manage the
pool of available agents by spawning, deleting, and inspecting them. It abstracts
the underlying container runtime (e.g., Docker, Kubernetes) by delegating actions
to a set of shell scripts. This tool is critical for dynamic resource allocation,
allowing the system to scale its agent workforce in response to changing demand
or to deploy specialized agents for specific tasks. It also integrates with the
Agent Registry to manage `hotpath` status, ensuring that critical agents are not
terminated prematurely.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Literal

import httpx

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator
from quadracode_contracts import DEFAULT_WORKSPACE_MOUNT
from .agent_registry import _registry_base_url, DEFAULT_TIMEOUT


class AgentManagementRequest(BaseModel):
    """Input contract for dynamic agent lifecycle management operations.

    This Pydantic schema validates requests for the `agent_management_tool`. It defines
    the set of legal operations and ensures that required parameters, like `agent_id`
    for deletion, are present. It also handles workspace mounting options, allowing
    a spawner to attach a persistent volume to a new agent, which is essential for
    tasks like debugging or stateful analysis.
    """

    operation: Literal[
        "spawn_agent",
        "delete_agent",
        "list_containers",
        "get_container_status",
        "mark_hotpath",
        "clear_hotpath",
        "list_hotpath",
    ] = Field(
        ...,
        description="Agent management operation to perform: "
        "spawn_agent|delete_agent|list_containers|get_container_status|mark_hotpath|clear_hotpath|list_hotpath",
    )
    agent_id: str | None = Field(
        default=None,
        description="Agent identifier. Required for delete_agent and get_container_status. "
        "Optional for spawn_agent (will be auto-generated if not provided).",
    )
    image: str | None = Field(
        default=None,
        description="Docker image name for spawning agent. Defaults to 'quadracode-agent'.",
    )
    network: str | None = Field(
        default=None,
        description="Docker network to attach the agent to. Defaults to 'quadracode_default'.",
    )
    workspace_id: str | None = Field(
        default=None,
        description="Optional workspace identifier to mount into the agent container.",
    )
    workspace_volume: str | None = Field(
        default=None,
        description="Optional Docker volume name backing the workspace to mount at /workspace.",
    )
    workspace_mount: str | None = Field(
        default=None,
        description="Mount path inside the agent container for the workspace volume (default /workspace).",
    )

    @model_validator(mode="after")
    def _validate_requirements(self) -> "AgentManagementRequest":
        if self.operation in {"delete_agent", "get_container_status", "mark_hotpath", "clear_hotpath"} and not self.agent_id:
            raise ValueError(f"{self.operation} requires agent_id")

        if self.workspace_mount and not self.workspace_volume:
            raise ValueError("workspace_mount requires workspace_volume")

        return self


def _get_scripts_dir() -> Path:
    """Locates the directory containing agent management shell scripts.

    This function provides a reliable way to find the necessary helper scripts
    (e.g., `spawn-agent.sh`) regardless of the execution context. It prioritizes
    an environment variable (`QUADRACODE_SCRIPTS_DIR`), then attempts to find the
    scripts relative to the tool's file location (common in development), and
    finally falls back to a hardcoded path (`/app/scripts`) used in the Docker
    production environment.
    """
    env_scripts_dir = os.environ.get("QUADRACODE_SCRIPTS_DIR")
    if env_scripts_dir:
        return Path(env_scripts_dir)

    # Try to find scripts directory relative to this file
    # This file is in: quadracode-tools/src/quadracode_tools/tools/
    # Scripts are in: scripts/agent-management/
    tool_file = Path(__file__)
    repo_root = tool_file.parents[4]  # Go up 4 levels to repo root
    scripts_dir = repo_root / "scripts" / "agent-management"

    if scripts_dir.exists():
        return scripts_dir

    # Fallback: assume scripts are in /app/scripts (Docker container path)
    return Path("/app/scripts/agent-management")


def _run_script(script_name: str, *args: str, env_overrides: dict[str, str] | None = None) -> dict:
    """Executes a specified agent management script in a subprocess and captures its output.

    This function is a generic wrapper for running the shell scripts that implement
    the agent management logic. It constructs the full script path, executes it with
    the provided arguments and environment overrides, and handles timeouts and other
    execution errors. It expects the script to output a JSON object to stdout and
    parses it, falling back to a structured error dictionary if the output is not
    valid JSON. This ensures that the tool always returns a machine-readable result.
    """
    scripts_dir = _get_scripts_dir()
    script_path = scripts_dir / script_name

    if not script_path.exists():
        return {
            "success": False,
            "error": f"Script not found: {script_path}",
            "message": f"Agent management script {script_name} not found at {script_path}",
        }

    env = os.environ.copy()
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                continue
            env[str(key)] = str(value)

    try:
        result = subprocess.run(
            [str(script_path)] + list(args),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=env,
        )

        # Try to parse JSON output
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            # If stdout isn't JSON, construct error response
            return {
                "success": False,
                "error": "Script output was not valid JSON",
                "message": f"Script execution failed or returned invalid JSON",
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Script execution timed out",
            "message": f"Script {script_name} took longer than 30 seconds to execute",
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"Failed to execute script {script_name}: {e}",
        }


@tool(args_schema=AgentManagementRequest)
def agent_management_tool(
    operation: str,
    agent_id: str | None = None,
    image: str | None = None,
    network: str | None = None,
    workspace_id: str | None = None,
    workspace_volume: str | None = None,
    workspace_mount: str | None = None,
) -> str:
    """Manages the lifecycle of Quadracode agents by spawning, deleting, or inspecting them.

    This tool serves as the primary interface for an orchestrator agent to control the
    agent workforce. It delegates its operations to underlying shell scripts, which
    abstract the specifics of the container runtime (e.g., Docker).

    Operations:
    - `spawn_agent`: Launches a new agent container. Can be customized with a specific
      image, network, and workspace volume.
    - `delete_agent`: Stops and removes an existing agent container.
    - `list_containers`: Returns a list of all active agent containers.
    - `get_container_status`: Fetches detailed information about a specific agent.
    - `mark_hotpath`/`clear_hotpath`: Sets or unsets an agent's `hotpath` flag in the
      registry, which can protect it from automated deletion.
    - `list_hotpath`: Retrieves all agents currently marked as `hotpath`.

    The tool is designed to be platform-agnostic, relying on the environment where
    it's executed to have the appropriate scripts and CLI tools (like `docker` or
    `kubectl`) available.
    """

    params = AgentManagementRequest(
        operation=operation,  # type: ignore[arg-type]
        agent_id=agent_id,
        image=image,
        network=network,
    )

    if params.operation == "spawn_agent":
        args = []
        if params.agent_id:
            args.append(params.agent_id)
        if params.image:
            if not args:
                args.append("")  # placeholder for agent_id
            args.append(params.image)
        if params.network:
            while len(args) < 2:
                args.append("")  # placeholders
            args.append(params.network)
        workspace_id = params.workspace_id
        workspace_volume = params.workspace_volume
        workspace_mount = params.workspace_mount

        descriptor_env: dict[str, Any] | None = None
        descriptor_raw = os.environ.get("QUADRACODE_ACTIVE_WORKSPACE_DESCRIPTOR")
        if descriptor_raw:
            try:
                parsed = json.loads(descriptor_raw)
                if isinstance(parsed, dict):
                    descriptor_env = parsed
            except json.JSONDecodeError:
                descriptor_env = None

        if descriptor_env:
            workspace_id = workspace_id or descriptor_env.get("workspace_id")
            workspace_volume = workspace_volume or descriptor_env.get("volume")
            workspace_mount = workspace_mount or descriptor_env.get("mount_path")

        if workspace_mount is None:
            workspace_mount = DEFAULT_WORKSPACE_MOUNT

        env_overrides: dict[str, str] = {}
        if workspace_id:
            env_overrides["QUADRACODE_WORKSPACE_ID"] = str(workspace_id)
        if workspace_volume:
            env_overrides["QUADRACODE_WORKSPACE_VOLUME"] = str(workspace_volume)
            env_overrides["QUADRACODE_WORKSPACE_MOUNT"] = str(workspace_mount or DEFAULT_WORKSPACE_MOUNT)

        response = _run_script(
            "spawn-agent.sh",
            *args,
            env_overrides=env_overrides or None,
        )
        return json.dumps(response, indent=2)

    if params.operation == "delete_agent":
        if params.agent_id and _is_hotpath_agent(params.agent_id):
            return json.dumps(
                {
                    "success": False,
                    "error": "hotpath_agent",
                    "message": (
                        f"Agent {params.agent_id} is marked as hotpath and must be cleared before deletion."
                    ),
                },
                indent=2,
            )
        response = _run_script("delete-agent.sh", params.agent_id)  # type: ignore[arg-type]
        return json.dumps(response, indent=2)

    if params.operation == "list_containers":
        response = _run_script("list-agents.sh")
        return json.dumps(response, indent=2)

    if params.operation == "get_container_status":
        response = _run_script("get-agent-status.sh", params.agent_id)  # type: ignore[arg-type]
        return json.dumps(response, indent=2)

    if params.operation == "mark_hotpath":
        return json.dumps(_update_hotpath_flag(params.agent_id, True), indent=2)

    if params.operation == "clear_hotpath":
        return json.dumps(_update_hotpath_flag(params.agent_id, False), indent=2)

    if params.operation == "list_hotpath":
        return json.dumps(_list_hotpath_agents(), indent=2)

    return json.dumps(
        {
            "success": False,
            "error": f"Unsupported operation: {params.operation}",
        },
        indent=2,
    )


# Ensure stable tool name
agent_management_tool.name = "agent_management"


REGISTRY_TIMEOUT = float(os.environ.get("AGENT_MANAGEMENT_REGISTRY_TIMEOUT", "5"))


def _registry_request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[bool, Any]:
    """A helper function for making direct HTTP requests to the agent registry."""
    base_url = _registry_base_url()
    url = f"{base_url}{path}"
    try:
        with httpx.Client(timeout=REGISTRY_TIMEOUT) as client:
            resp = client.request(method, url, json=payload)
            resp.raise_for_status()
            if not resp.content:
                return True, {}
            try:
                return True, resp.json()
            except ValueError:
                return True, resp.text
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip() or exc.response.reason_phrase
        return False, f"Registry request failed ({exc.response.status_code}): {detail}"
    except httpx.RequestError as exc:
        return False, f"Unable to reach agent registry at {base_url}: {exc}"


def _is_hotpath_agent(agent_id: str) -> bool:
    """Checks if a given agent is currently marked as a 'hotpath' agent in the registry.

    Hotpath agents are typically protected from automated scale-down or deletion.
    This function queries the registry's `/agents/{agent_id}` endpoint to determine
    the agent's status.
    """
    ok, payload = _registry_request("GET", f"/agents/{agent_id}")
    if not ok or not isinstance(payload, dict):
        return False
    return bool(payload.get("hotpath"))


def _update_hotpath_flag(agent_id: str | None, hotpath: bool) -> dict[str, Any]:
    """Sets or clears the 'hotpath' flag for an agent via the registry API."""
    if not agent_id:
        return {"success": False, "error": "agent_id_required"}
    ok, payload = _registry_request(
        "POST",
        f"/agents/{agent_id}/hotpath",
        {"hotpath": hotpath},
    )
    if not ok or not isinstance(payload, dict):
        return {"success": False, "error": payload}
    status = "marked" if hotpath else "cleared"
    return {
        "success": True,
        "message": f"Agent {agent_id} hotpath flag {status}.",
        "agent": payload,
    }


def _list_hotpath_agents() -> dict[str, Any]:
    """Retrieves a list of all agents currently marked as 'hotpath' from the registry."""
    ok, payload = _registry_request("GET", "/agents/hotpath")
    if not ok or not isinstance(payload, dict):
        return {"success": False, "error": payload}
    agents = payload.get("agents", [])
    return {
        "success": True,
        "agents": agents,
        "count": len(agents),
    }

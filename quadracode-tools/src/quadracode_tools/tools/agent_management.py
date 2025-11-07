from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Literal, Optional

import httpx

from langchain_core.tools import tool
from pydantic import BaseModel, Field, root_validator
from quadracode_contracts import DEFAULT_WORKSPACE_MOUNT
from .agent_registry import _registry_base_url, DEFAULT_TIMEOUT


class AgentManagementRequest(BaseModel):
    """Input contract for dynamic agent lifecycle management."""

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
    agent_id: Optional[str] = Field(
        default=None,
        description="Agent identifier. Required for delete_agent and get_container_status. "
        "Optional for spawn_agent (will be auto-generated if not provided).",
    )
    image: Optional[str] = Field(
        default=None,
        description="Docker image name for spawning agent. Defaults to 'quadracode-agent'.",
    )
    network: Optional[str] = Field(
        default=None,
        description="Docker network to attach the agent to. Defaults to 'quadracode_default'.",
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description="Optional workspace identifier to mount into the agent container.",
    )
    workspace_volume: Optional[str] = Field(
        default=None,
        description="Optional Docker volume name backing the workspace to mount at /workspace.",
    )
    workspace_mount: Optional[str] = Field(
        default=None,
        description="Mount path inside the agent container for the workspace volume (default /workspace).",
    )

    @root_validator(skip_on_failure=True)
    def _validate_requirements(cls, values):  # type: ignore[override]
        op = values.get("operation")
        agent_id = values.get("agent_id")

        if op in {"delete_agent", "get_container_status", "mark_hotpath", "clear_hotpath"} and not agent_id:
            raise ValueError(f"{op} requires agent_id")

        workspace_volume = values.get("workspace_volume")
        workspace_mount = values.get("workspace_mount")
        if workspace_mount and not workspace_volume:
            raise ValueError("workspace_mount requires workspace_volume")

        return values


def _get_scripts_dir() -> Path:
    """
    Find the scripts directory.

    Looks for QUADRACODE_SCRIPTS_DIR environment variable first,
    otherwise tries to find scripts relative to the tool location.
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


def _run_script(script_name: str, *args: str, env_overrides: Optional[Dict[str, str]] = None) -> dict:
    """
    Execute an agent management script and return parsed JSON output.

    Args:
        script_name: Name of the script (e.g., "spawn-agent.sh")
        *args: Arguments to pass to the script

    Returns:
        Parsed JSON response from the script
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
) -> str:
    """Manage the lifecycle of Quadracode agents dynamically.

    This tool allows the orchestrator to spawn new agents when additional capacity
    or specialized capabilities are needed, and to delete agents when they are no
    longer required.

    Operations:
    - `spawn_agent`: Launch a new agent container/pod
      - Optional `agent_id` (auto-generated if not provided)
      - Optional `image` (defaults to 'quadracode-agent')
      - Optional `network` (defaults to 'quadracode_default' for Docker)
    - `delete_agent`: Stop and remove an agent container/pod
      - Requires `agent_id`
    - `list_containers`: List all running agent containers/pods
    - `get_container_status`: Get detailed status of a specific agent
      - Requires `agent_id`
    - `mark_hotpath` / `clear_hotpath`: Toggle registry hotpath residency (requires `agent_id`)
    - `list_hotpath`: Inspect all agents marked as hotpath in the registry

    Platform Support:
    - Docker: Default, uses Docker CLI and docker-compose networks
    - Kubernetes: Set AGENT_RUNTIME_PLATFORM=kubernetes
      - Requires kubectl access and quadracode-secrets configured
      - Uses PVCs: quadracode-shared-data, quadracode-mcp-cache

    Examples:
    - {"operation": "spawn_agent"}
    - {"operation": "spawn_agent", "agent_id": "specialized-agent"}
    - {"operation": "delete_agent", "agent_id": "agent-abc123"}
    - {"operation": "list_containers"}
    - {"operation": "get_container_status", "agent_id": "agent-abc123"}

    The spawned agents will automatically:
    1. Connect to Redis at the configured REDIS_HOST:REDIS_PORT
    2. Register with the agent registry
    3. Begin polling their mailbox for work
    4. Report health via heartbeats
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

        descriptor_env: Optional[dict[str, Any]] = None
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

        env_overrides: Dict[str, str] = {}
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


def _registry_request(method: str, path: str, payload: Dict[str, Any] | None = None) -> tuple[bool, Any]:
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
    ok, payload = _registry_request("GET", f"/agents/{agent_id}")
    if not ok or not isinstance(payload, dict):
        return False
    return bool(payload.get("hotpath"))


def _update_hotpath_flag(agent_id: Optional[str], hotpath: bool) -> Dict[str, Any]:
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


def _list_hotpath_agents() -> Dict[str, Any]:
    ok, payload = _registry_request("GET", "/agents/hotpath")
    if not ok or not isinstance(payload, dict):
        return {"success": False, "error": payload}
    agents = payload.get("agents", [])
    return {
        "success": True,
        "agents": agents,
        "count": len(agents),
    }

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, root_validator


class AgentManagementRequest(BaseModel):
    """Input contract for dynamic agent lifecycle management."""

    operation: Literal[
        "spawn_agent",
        "delete_agent",
        "list_containers",
        "get_container_status",
    ] = Field(
        ...,
        description="Agent management operation to perform: "
        "spawn_agent|delete_agent|list_containers|get_container_status",
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

    @root_validator(skip_on_failure=True)
    def _validate_requirements(cls, values):  # type: ignore[override]
        op = values.get("operation")
        agent_id = values.get("agent_id")

        if op in {"delete_agent", "get_container_status"} and not agent_id:
            raise ValueError(f"{op} requires agent_id")

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


def _run_script(script_name: str, *args: str) -> dict:
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

    try:
        result = subprocess.run(
            [str(script_path)] + list(args),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
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

        response = _run_script("spawn-agent.sh", *args)
        return json.dumps(response, indent=2)

    if params.operation == "delete_agent":
        response = _run_script("delete-agent.sh", params.agent_id)  # type: ignore[arg-type]
        return json.dumps(response, indent=2)

    if params.operation == "list_containers":
        response = _run_script("list-agents.sh")
        return json.dumps(response, indent=2)

    if params.operation == "get_container_status":
        response = _run_script("get-agent-status.sh", params.agent_id)  # type: ignore[arg-type]
        return json.dumps(response, indent=2)

    return json.dumps({
        "success": False,
        "error": f"Unsupported operation: {params.operation}",
    }, indent=2)


# Ensure stable tool name
agent_management_tool.name = "agent_management"

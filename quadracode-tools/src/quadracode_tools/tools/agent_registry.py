from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field, root_validator

DEFAULT_TIMEOUT = 10.0


class AgentRegistryRequest(BaseModel):
    """Input contract for interacting with the agent registry service."""

    operation: Literal[
        "list_agents",
        "get_agent",
        "register_agent",
        "heartbeat",
        "unregister_agent",
        "stats",
        "health",
    ] = Field(
        ...,
        description="Registry operation to perform."
        " list_agents|get_agent|register_agent|heartbeat|unregister_agent|stats|health",
    )
    healthy_only: bool = Field(
        default=False,
        description="Filter to healthy agents when listing.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of agents to include in list summaries.",
    )
    agent_id: Optional[str] = Field(
        default=None,
        description="Target agent identifier (required for single-agent operations).",
    )
    host: Optional[str] = Field(
        default=None,
        description="Agent host when registering a new agent.",
    )
    port: Optional[int] = Field(
        default=None,
        ge=0,
        le=65535,
        description="Agent port when registering a new agent.",
    )
    status: Optional[Literal["healthy", "unhealthy"]] = Field(
        default=None,
        description="Reported status for heartbeat calls (defaults to healthy).",
    )
    reported_at: Optional[datetime] = Field(
        default=None,
        description="Heartbeat timestamp (defaults to current UTC time).",
    )

    @root_validator(skip_on_failure=True)
    def _validate_requirements(cls, values):  # type: ignore[override]
        op = values.get("operation")
        agent_id = values.get("agent_id")
        if op in {"get_agent", "heartbeat", "unregister_agent"} and not agent_id:
            raise ValueError("agent_id is required for the requested operation")
        if op == "register_agent":
            missing = [name for name in ("agent_id", "host", "port") if values.get(name) in (None, "")]
            if missing:
                raise ValueError(
                    "register_agent requires agent_id, host, and port"
                )
        return values


def _registry_base_url() -> str:
    base = os.environ.get("AGENT_REGISTRY_URL", "http://quadracode-agent-registry:8090")
    return base.rstrip("/")


def _format_json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


@tool(args_schema=AgentRegistryRequest)
def agent_registry_tool(
    operation: str,
    healthy_only: bool = False,
    limit: int = 50,
    agent_id: str | None = None,
    host: str | None = None,
    port: int | None = None,
    status: str | None = None,
    reported_at: datetime | None = None,
) -> str:
    """Call the Quadracode Agent Registry REST API.

    Set `operation` to one of:
    - `list_agents` (optionally `healthy_only` and `limit`)
    - `get_agent` (requires `agent_id`)
    - `register_agent` (requires `agent_id`, `host`, `port`)
    - `heartbeat` (requires `agent_id`; optional `status`, `reported_at`)
    - `unregister_agent` (requires `agent_id`)
    - `stats`
    - `health`

    The `AgentRegistryRequest` schema handles validation so callers only need
    to supply the relevant fields for the chosen operation.

    Examples:
    - {"operation": "list_agents", "healthy_only": true}
    - {"operation": "register_agent", "agent_id": "alpha", "host": "localhost", "port": 8080}
    - {"operation": "heartbeat", "agent_id": "alpha", "status": "healthy"}
    """

    params = AgentRegistryRequest(
        operation=operation,  # type: ignore[arg-type]
        healthy_only=healthy_only,
        limit=limit,
        agent_id=agent_id,
        host=host,
        port=port,
        status=status,  # type: ignore[arg-type]
        reported_at=reported_at,
    )

    base_url = _registry_base_url()

    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            if params.operation == "list_agents":
                params_dict: dict[str, str] = {}
                if params.healthy_only:
                    params_dict["healthy_only"] = "true"
                resp = client.get(f"{base_url}/agents", params=params_dict or None)
                resp.raise_for_status()
                payload = resp.json()
                agents = payload.get("agents") if isinstance(payload, dict) else payload
                agents = agents or []
                total = len(agents)
                agents = agents[: params.limit]
                if not agents:
                    qualifier = " healthy" if params.healthy_only else ""
                    return f"No{qualifier} agents registered at {base_url}."

                lines = [
                    f"Listing {len(agents)} of {total} agent(s) (healthy_only={params.healthy_only})."
                ]
                for agent in agents:
                    agent_id = agent.get("agent_id", "<unknown>")
                    host = agent.get("host", "?")
                    port = agent.get("port", "?")
                    status = agent.get("status", "unknown")
                    last_hb = agent.get("last_heartbeat") or "never"
                    lines.append(
                        f"- {agent_id} @ {host}:{port} status={status} last_heartbeat={last_hb}"
                    )
                return "\n".join(lines)

            if params.operation == "get_agent":
                resp = client.get(f"{base_url}/agents/{params.agent_id}")
                resp.raise_for_status()
                return _format_json(resp.json())

            if params.operation == "register_agent":
                body = {
                    "agent_id": params.agent_id,
                    "host": params.host,
                    "port": params.port,
                }
                resp = client.post(f"{base_url}/agents/register", json=body)
                resp.raise_for_status()
                return f"Registered agent {params.agent_id} ({params.host}:{params.port})."

            if params.operation == "heartbeat":
                reported_at = params.reported_at or datetime.utcnow()
                if reported_at.tzinfo is not None:
                    reported_at = reported_at.astimezone(timezone.utc).replace(tzinfo=None)
                heartbeat_body = {
                    "agent_id": params.agent_id,
                    "status": params.status or "healthy",
                    "reported_at": reported_at.isoformat(),
                }
                resp = client.post(
                    f"{base_url}/agents/{params.agent_id}/heartbeat",
                    json=heartbeat_body,
                )
                resp.raise_for_status()
                return f"Heartbeat recorded for {params.agent_id} (status={heartbeat_body['status']})."

            if params.operation == "unregister_agent":
                resp = client.delete(f"{base_url}/agents/{params.agent_id}")
                resp.raise_for_status()
                return f"Unregistered agent {params.agent_id}."

            if params.operation == "stats":
                resp = client.get(f"{base_url}/stats")
                resp.raise_for_status()
                stats = resp.json()
                if not isinstance(stats, dict):
                    return _format_json(stats)
                total = stats.get("total_agents")
                healthy = stats.get("healthy_agents")
                unhealthy = stats.get("unhealthy_agents")
                last_updated = stats.get("last_updated", "unknown")
                return (
                    f"Registry stats: total={total} healthy={healthy} unhealthy={unhealthy}"
                    f" (last_updated={last_updated})."
                )

            if params.operation == "health":
                resp = client.get(f"{base_url}/health")
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, dict) and "status" in payload:
                    return f"Registry health: {payload['status']}."
                return _format_json(payload)

    except httpx.HTTPStatusError as exc:
        detail = exc.response.text.strip()
        return (
            f"Registry request failed ({exc.response.status_code}): "
            f"{detail or exc.response.reason_phrase}"
        )
    except httpx.RequestError as exc:
        return f"Unable to reach agent registry at {base_url}: {exc}"

    return "Unsupported operation requested."


# Ensure stable tool name across langchain-core versions.
agent_registry_tool.name = "agent_registry"

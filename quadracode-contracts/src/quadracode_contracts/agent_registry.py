"""
Pydantic data models serving as the shared contract for the Quadracode Agent
Registry service.

These models ensure type-safe and validated data exchange between the agent
registry and its clients (agents, orchestrator).  The contracts are minimal
and efficient, supporting a lightweight registration and discovery protocol.
Centralizing these schemas provides a single source of truth for the
registry's API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class AgentStatus(str, Enum):
    """Possible health statuses of an agent.

    Attributes:
        HEALTHY: The agent is responsive and operating normally.
        UNHEALTHY: The agent has missed heartbeats and is considered stale.
    """

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class AgentRegistrationRequest(BaseModel):
    """Payload sent by an agent when it registers with the registry service.

    Captures the essential information required for the registry to track a
    new agent, including its unique ID and network location.
    """

    model_config = ConfigDict(extra="ignore")

    agent_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for the agent.",
    )
    host: str = Field(
        ...,
        min_length=1,
        description="Hostname or IP address reachable by the orchestrator.",
    )
    port: int = Field(
        ...,
        ge=1,
        le=65535,
        description="Primary service port exposed by the agent.",
    )


class AgentHeartbeat(BaseModel):
    """Heartbeat payload reported by an agent to indicate liveness.

    Used to update an agent's status in the registry, keeping it marked as
    healthy.
    """

    agent_id: str = Field(
        ...,
        min_length=1,
        description="Agent identifier sending the heartbeat.",
    )
    status: AgentStatus = Field(
        default=AgentStatus.HEALTHY,
        description="Reported health status.",
    )
    reported_at: datetime = Field(
        default_factory=_utc_now,
        description="Heartbeat timestamp.",
    )


class AgentInfo(BaseModel):
    """Full record for an agent as maintained by the registry service.

    Used in API responses to provide detailed information about a registered
    agent, including its status and timestamps.
    """

    agent_id: str = Field(..., min_length=1)
    host: str
    port: int = Field(..., ge=1, le=65535)
    status: AgentStatus
    registered_at: datetime
    last_heartbeat: datetime | None = None


class AgentListResponse(BaseModel):
    """Response envelope when returning a list of agents.

    Wraps the list of agents and includes metadata about any filters that
    were applied.
    """

    agents: list[AgentInfo]
    healthy_only: bool = Field(
        default=False,
        description="Whether unhealthy agents were filtered.",
    )


class RegistryStats(BaseModel):
    """Aggregate statistics exposed by the registry service.

    Provides a snapshot of the registry's state — total, healthy, and
    unhealthy agent counts.
    """

    total_agents: int = Field(..., ge=0)
    healthy_agents: int = Field(..., ge=0)
    unhealthy_agents: int = Field(..., ge=0)
    last_updated: datetime = Field(default_factory=_utc_now)


__all__ = [
    "AgentStatus",
    "AgentRegistrationRequest",
    "AgentHeartbeat",
    "AgentInfo",
    "AgentListResponse",
    "RegistryStats",
]

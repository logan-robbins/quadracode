"""Pydantic v2 request/response schemas for the Agent Registry API.

All models use Python 3.12+ built-in generics (``list``, ``dict``, ``X | None``)
and timezone-aware ``datetime.now(timezone.utc)`` instead of the deprecated
``datetime.utcnow()``.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(str, Enum):
    """Health status of a registered agent."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class AgentRegistrationRequest(BaseModel):
    """Payload sent by an agent on startup to register with the service."""

    model_config = ConfigDict(strict=False)

    agent_id: str = Field(..., description="Unique identifier for the agent")
    host: str = Field(
        ..., description="Hostname or IP address reachable by the orchestrator"
    )
    port: int = Field(
        ..., ge=1, le=65535, description="Primary service port exposed by the agent"
    )
    hotpath: bool = Field(
        default=False,
        description="Mark the agent as resident (hotpath) so it is never scaled down.",
    )


class AgentHeartbeat(BaseModel):
    """Heartbeat payload reported by an agent to signal liveness.

    ``agent_id`` is injected from the URL path parameter by the API handler.
    """

    model_config = ConfigDict(strict=False)

    agent_id: str = Field(
        default="", description="Agent identifier (set from URL path)"
    )
    status: AgentStatus = Field(
        default=AgentStatus.HEALTHY, description="Reported health status"
    )
    reported_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Heartbeat timestamp (UTC)",
    )
    metrics: dict | None = Field(
        default=None, description="Optional system metrics and process info"
    )


class AgentInfo(BaseModel):
    """Full agent record as maintained by the registry."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: str
    host: str
    port: int
    status: AgentStatus
    registered_at: datetime
    last_heartbeat: datetime | None = None
    hotpath: bool = Field(default=False)
    metrics: dict | None = None


class AgentListResponse(BaseModel):
    """Response envelope for agent listing endpoints."""

    agents: list[AgentInfo]
    healthy_only: bool = Field(default=False)
    hotpath_only: bool = Field(default=False)


class HotpathUpdateRequest(BaseModel):
    """Request payload to toggle an agent's hotpath designation."""

    hotpath: bool = Field(..., description="Desired hotpath flag value")


class RegistryStats(BaseModel):
    """Aggregate statistics snapshot of the registry."""

    total_agents: int
    healthy_agents: int
    unhealthy_agents: int
    last_updated: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"


class StatusResponse(BaseModel):
    """Generic operation status response."""

    status: str


class ErrorDetail(BaseModel):
    """Structured error response body."""

    detail: str


__all__ = [
    "AgentStatus",
    "AgentRegistrationRequest",
    "AgentHeartbeat",
    "AgentInfo",
    "AgentListResponse",
    "HotpathUpdateRequest",
    "RegistryStats",
    "HealthResponse",
    "StatusResponse",
    "ErrorDetail",
]

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class AgentRegistrationRequest(BaseModel):
    """Payload sent by agents when registering with the registry service."""

    agent_id: str = Field(..., description="Unique identifier for the agent")
    host: str = Field(..., description="Hostname or IP address reachable by the orchestrator")
    port: int = Field(..., description="Primary service port exposed by the agent")
    hotpath: bool = Field(
        default=False,
        description="Mark the agent as resident (hotpath) so it is never scaled down automatically.",
    )


class AgentHeartbeat(BaseModel):
    """Heartbeat payload reported by agents to indicate liveness."""

    agent_id: str = Field(..., description="Agent identifier sending the heartbeat")
    status: AgentStatus = Field(default=AgentStatus.HEALTHY, description="Reported health status")
    reported_at: datetime = Field(default_factory=datetime.utcnow, description="Heartbeat timestamp")


class AgentInfo(BaseModel):
    """Full record for an agent maintained by the registry service."""

    agent_id: str
    host: str
    port: int
    status: AgentStatus
    registered_at: datetime
    last_heartbeat: Optional[datetime]
    hotpath: bool = Field(default=False)


class AgentListResponse(BaseModel):
    """Response envelope returning a set of agents to callers."""

    agents: List[AgentInfo]
    healthy_only: bool = Field(default=False)
    hotpath_only: bool = Field(default=False)


class HotpathUpdateRequest(BaseModel):
    """Request payload for toggling an agent's hotpath state."""

    hotpath: bool = Field(..., description="Desired hotpath flag value")


class RegistryStats(BaseModel):
    """Aggregate statistics exposed by the registry service."""

    total_agents: int
    healthy_agents: int
    unhealthy_agents: int
    last_updated: datetime = Field(default_factory=datetime.utcnow)


__all__ = [
    "AgentStatus",
    "AgentRegistrationRequest",
    "AgentHeartbeat",
    "AgentInfo",
    "AgentListResponse",
    "RegistryStats",
    "HotpathUpdateRequest",
]

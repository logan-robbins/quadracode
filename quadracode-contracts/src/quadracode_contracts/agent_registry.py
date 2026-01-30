"""
This module defines the Pydantic data models that serve as the shared contract 
for the Quadracode Agent Registry service.

These models ensure type-safe and validated data exchange between the agent 
registry and its clients (such as agents and the orchestrator). The contracts 
are designed to be minimal and efficient, supporting a lightweight registration 
and discovery protocol. By centralizing these schemas, this module provides a 
single source of truth for the registry's API, which simplifies client 
implementations and ensures consistency.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    """Returns the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class AgentStatus(str, Enum):
    """
    Enumeration for the possible health statuses of an agent.

    Attributes:
        HEALTHY: The agent is responsive and operating normally.
        UNHEALTHY: The agent has missed its heartbeats and is considered stale.
    """
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class AgentRegistrationRequest(BaseModel):
    """
    Data model for the payload sent by an agent when it registers with the 
    registry service.

    This model captures the essential information required for the registry to 
    track a new agent, including its unique ID and network location.
    """

    agent_id: str = Field(..., description="Unique identifier for the agent")
    host: str = Field(..., description="Hostname or IP address reachable by the orchestrator")
    port: int = Field(..., description="Primary service port exposed by the agent")


class AgentHeartbeat(BaseModel):
    """
    Data model for the heartbeat payload reported by an agent to indicate 
    liveness.

    This model is used to update an agent's status in the registry, keeping it 
    marked as healthy.
    """

    agent_id: str = Field(..., description="Agent identifier sending the heartbeat")
    status: AgentStatus = Field(default=AgentStatus.HEALTHY, description="Reported health status")
    reported_at: datetime = Field(default_factory=_utc_now, description="Heartbeat timestamp")


class AgentInfo(BaseModel):
    """
    Represents the full record for an agent as maintained by the registry 
    service.

    This model is used in API responses to provide detailed information about a 
    registered agent, including its status and timestamps.
    """

    agent_id: str
    host: str
    port: int
    status: AgentStatus
    registered_at: datetime
    last_heartbeat: Optional[datetime] = None


class AgentListResponse(BaseModel):
    """
    Data model for the response envelope when returning a list of agents.

    This model wraps the list of agents and includes metadata about any filters 
    that were applied to the request.
    """

    agents: List[AgentInfo]
    healthy_only: bool = Field(default=False, description="Whether unhealthy agents were filtered")


class RegistryStats(BaseModel):
    """
    Data model for the aggregate statistics exposed by the registry service.

    This model provides a snapshot of the registry's state, including the 
    number of total, healthy, and unhealthy agents.
    """

    total_agents: int
    healthy_agents: int
    unhealthy_agents: int
    last_updated: datetime = Field(default_factory=_utc_now)


__all__ = [
    "AgentStatus",
    "AgentRegistrationRequest",
    "AgentHeartbeat",
    "AgentInfo",
    "AgentListResponse",
    "RegistryStats",
]

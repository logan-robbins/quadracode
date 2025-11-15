"""
This module defines the Pydantic data models used for API requests and responses 
in the Quadracode Agent Registry.

These models, referred to as schemas, ensure that all data exchanged with the 
API is strongly-typed and validated. They serve as the single source of truth 
for the data contracts of the service, and are used by FastAPI to automatically 
generate OpenAPI documentation and perform data validation. This strict typing 
is crucial for maintaining a robust and reliable API.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


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

    This model captures all the necessary information for the registry to track 
    a new agent, including its unique ID, network location, and whether it 
    should be treated as a resident (hotpath) agent.
    """

    agent_id: str = Field(..., description="Unique identifier for the agent")
    host: str = Field(..., description="Hostname or IP address reachable by the orchestrator")
    port: int = Field(..., description="Primary service port exposed by the agent")
    hotpath: bool = Field(
        default=False,
        description="Mark the agent as resident (hotpath) so it is never scaled down automatically.",
    )


class AgentHeartbeat(BaseModel):
    """
    Data model for the heartbeat payload reported by an agent to indicate 
    liveness.

    This model is used to update the agent's status in the registry, keeping 
    it marked as healthy.
    """

    agent_id: str = Field(..., description="Agent identifier sending the heartbeat")
    status: AgentStatus = Field(default=AgentStatus.HEALTHY, description="Reported health status")
    reported_at: datetime = Field(default_factory=datetime.utcnow, description="Heartbeat timestamp")


class AgentInfo(BaseModel):
    """
    Represents the full record for an agent as maintained by the registry 
    service.

    This model is used in API responses to provide detailed information about a 
    registered agent.
    """

    agent_id: str
    host: str
    port: int
    status: AgentStatus
    registered_at: datetime
    last_heartbeat: Optional[datetime]
    hotpath: bool = Field(default=False)


class AgentListResponse(BaseModel):
    """
    Data model for the response envelope when returning a list of agents.

    This model wraps the list of agents and includes metadata about the filters 
    that were applied to the request.
    """

    agents: List[AgentInfo]
    healthy_only: bool = Field(default=False)
    hotpath_only: bool = Field(default=False)


class HotpathUpdateRequest(BaseModel):
    """
    Data model for the request payload used to toggle an agent's hotpath state.
    """

    hotpath: bool = Field(..., description="Desired hotpath flag value")


class RegistryStats(BaseModel):
    """
    Data model for the aggregate statistics exposed by the registry service.

    This model provides a snapshot of the registry's state, including the 
    number of total, healthy, and unhealthy agents.
    """

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

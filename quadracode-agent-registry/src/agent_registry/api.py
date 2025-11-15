"""
This module defines the FastAPI routes for the Quadracode Agent Registry.

It creates a FastAPI APIRouter and attaches endpoints for all agent management 
operations, including registration, health checks (heartbeats), listing, and 
hotpath management. The router is designed to be dynamically included in the main 
FastAPI application, with dependencies such as the `AgentRegistryService` 
injected at runtime. This modular approach keeps the API definitions separate 
from the application's core logic.
"""
from fastapi import APIRouter, HTTPException

from .schemas import (
    AgentHeartbeat,
    AgentInfo,
    AgentListResponse,
    AgentRegistrationRequest,
    HotpathUpdateRequest,
    RegistryStats,
)
from .service import AgentRegistryService


def get_router(service: AgentRegistryService) -> APIRouter:
    """
    Creates and configures the API router for the agent registry service.

    This function initializes a FastAPI APIRouter and defines all the HTTP 
    endpoints for interacting with the agent registry. It wires each endpoint to 
    the corresponding method in the `AgentRegistryService`, which encapsulates 
    the business logic.

    Args:
        service: An instance of `AgentRegistryService` that provides the 
                 business logic for agent registration and management.

    Returns:
        A configured `APIRouter` instance with all the agent registry 
        endpoints.
    """
    router = APIRouter()

    @router.get("/health")
    def health_check():
        """Provides a simple health check endpoint for the service."""
        return {"status": "healthy"}

    @router.post("/agents/register")
    def register_agent(reg: AgentRegistrationRequest):
        """
        Registers a new agent with the registry.

        This endpoint accepts an `AgentRegistrationRequest` and uses the 
        `AgentRegistryService` to persist the new agent's information.
        """
        service.register(reg)
        return {"status": "success"}

    @router.post("/agents/{agent_id}/heartbeat")
    def agent_heartbeat(agent_id: str, hb: AgentHeartbeat):
        """
        Receives a heartbeat from a registered agent to keep it alive.

        This endpoint is used by agents to signal that they are still active. 
        If an agent is not found, it returns a 404 error.
        """
        hb.agent_id = agent_id
        if not service.heartbeat(hb):
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "success"}

    @router.get("/agents", response_model=AgentListResponse)
    def list_agents(healthy_only: bool = False, hotpath_only: bool = False):
        """
        Lists all registered agents, with optional filters.

        This endpoint allows clients to retrieve a list of all agents, with 
        options to filter for only healthy agents or those on the hotpath.
        """
        return service.list_agents(healthy_only=healthy_only, hotpath_only=hotpath_only)

    @router.get("/agents/hotpath", response_model=AgentListResponse)
    def list_hotpath_agents():
        """Lists all agents currently assigned to the hotpath."""
        return service.list_agents(healthy_only=False, hotpath_only=True)

    @router.get("/agents/{agent_id}", response_model=AgentInfo)
    def get_agent(agent_id: str):
        """
        Retrieves detailed information for a specific agent.

        If the agent is not found in the registry, this endpoint returns a 404 
        error.
        """
        agent = service.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    @router.delete("/agents/{agent_id}")
    def unregister_agent(agent_id: str, force: bool = False):
        """
        Unregisters an agent from the registry.

        This endpoint removes an agent's registration. It includes a `force` 
        parameter to bypass certain safety checks, but will not remove a 
        hotpath agent unless forced.
        """
        try:
            if not service.remove_agent(agent_id, force=force):
                raise HTTPException(status_code=404, detail="Agent not found")
        except ValueError as exc:
            if str(exc) == "hotpath_agent":
                raise HTTPException(status_code=409, detail="Cannot remove hotpath agent")
            raise
        return {"status": "success"}

    @router.post("/agents/{agent_id}/hotpath", response_model=AgentInfo)
    def set_hotpath(agent_id: str, request: HotpathUpdateRequest):
        """
        Assigns or unassigns an agent to the hotpath.

        This endpoint is used to designate a specific agent as the primary 
        handler for high-priority tasks.
        """
        try:
            return service.set_hotpath(agent_id, request.hotpath)
        except ValueError as exc:
            if str(exc) == "agent_not_found":
                raise HTTPException(status_code=404, detail="Agent not found")
            raise

    @router.get("/stats", response_model=RegistryStats)
    def get_stats():
        """Retrieves statistics about the agent registry."""
        return service.stats()

    return router

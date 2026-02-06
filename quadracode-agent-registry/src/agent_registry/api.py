"""FastAPI HTTP endpoints for the Quadracode Agent Registry.

Defines an ``APIRouter`` with typed ``response_model`` declarations on every
endpoint and structured error responses.  The service dependency is injected
via closure from the application factory.
"""

from fastapi import APIRouter, HTTPException, status

from .schemas import (
    AgentHeartbeat,
    AgentInfo,
    AgentListResponse,
    AgentRegistrationRequest,
    ErrorDetail,
    HealthResponse,
    HotpathUpdateRequest,
    RegistryStats,
    StatusResponse,
)
from .service import AgentRegistryService


def get_router(service: AgentRegistryService) -> APIRouter:
    """Build the agent-registry API router.

    Args:
        service: Business-logic layer injected from the app factory.

    Returns:
        Configured ``APIRouter`` with all registry endpoints.
    """
    router = APIRouter()

    @router.get(
        "/health",
        response_model=HealthResponse,
        tags=["monitoring"],
        summary="Service health check",
    )
    def health_check() -> HealthResponse:
        """Lightweight liveness probe for load-balancer / Docker HEALTHCHECK."""
        return HealthResponse()

    @router.post(
        "/agents/register",
        response_model=AgentInfo,
        status_code=status.HTTP_201_CREATED,
        tags=["agents"],
        summary="Register an agent",
        responses={422: {"model": ErrorDetail}},
    )
    def register_agent(reg: AgentRegistrationRequest) -> AgentInfo:
        """Register (or re-register) an agent with the registry."""
        return service.register(reg)

    @router.post(
        "/agents/{agent_id}/heartbeat",
        response_model=StatusResponse,
        tags=["agents"],
        summary="Agent heartbeat",
        responses={404: {"model": ErrorDetail}},
    )
    def agent_heartbeat(agent_id: str, hb: AgentHeartbeat) -> StatusResponse:
        """Record a heartbeat for a registered agent."""
        hb.agent_id = agent_id
        if not service.heartbeat(hb):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )
        return StatusResponse(status="success")

    @router.get(
        "/agents",
        response_model=AgentListResponse,
        tags=["agents"],
        summary="List agents",
    )
    def list_agents(
        healthy_only: bool = False,
        hotpath_only: bool = False,
    ) -> AgentListResponse:
        """List all registered agents with optional health/hotpath filters."""
        return service.list_agents(
            healthy_only=healthy_only, hotpath_only=hotpath_only
        )

    @router.get(
        "/agents/hotpath",
        response_model=AgentListResponse,
        tags=["agents"],
        summary="List hotpath agents",
    )
    def list_hotpath_agents() -> AgentListResponse:
        """List agents currently pinned to the hotpath."""
        return service.list_agents(healthy_only=False, hotpath_only=True)

    @router.get(
        "/agents/{agent_id}",
        response_model=AgentInfo,
        tags=["agents"],
        summary="Get agent details",
        responses={404: {"model": ErrorDetail}},
    )
    def get_agent(agent_id: str) -> AgentInfo:
        """Retrieve detailed information for a specific agent."""
        agent = service.get_agent(agent_id)
        if not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent not found",
            )
        return agent

    @router.delete(
        "/agents/{agent_id}",
        response_model=StatusResponse,
        tags=["agents"],
        summary="Unregister an agent",
        responses={
            404: {"model": ErrorDetail},
            409: {"model": ErrorDetail},
        },
    )
    def unregister_agent(
        agent_id: str, force: bool = False
    ) -> StatusResponse:
        """Remove an agent from the registry.

        Hotpath agents are protected; pass ``force=true`` to override.
        """
        try:
            if not service.remove_agent(agent_id, force=force):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found",
                )
        except ValueError as exc:
            if str(exc) == "hotpath_agent":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot remove hotpath agent without force=true",
                ) from exc
            raise
        return StatusResponse(status="success")

    @router.post(
        "/agents/{agent_id}/hotpath",
        response_model=AgentInfo,
        tags=["agents"],
        summary="Set hotpath status",
        responses={404: {"model": ErrorDetail}},
    )
    def set_hotpath(agent_id: str, request: HotpathUpdateRequest) -> AgentInfo:
        """Assign or remove an agent from the hotpath."""
        try:
            return service.set_hotpath(agent_id, request.hotpath)
        except ValueError as exc:
            if str(exc) == "agent_not_found":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent not found",
                ) from exc
            raise

    @router.get(
        "/stats",
        response_model=RegistryStats,
        tags=["monitoring"],
        summary="Registry statistics",
    )
    def get_stats() -> RegistryStats:
        """Aggregate statistics about the agent registry."""
        return service.stats()

    return router

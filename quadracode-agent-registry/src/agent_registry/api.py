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
    router = APIRouter()

    @router.get("/health")
    def health_check():
        return {"status": "healthy"}

    @router.post("/agents/register")
    def register_agent(reg: AgentRegistrationRequest):
        service.register(reg)
        return {"status": "success"}

    @router.post("/agents/{agent_id}/heartbeat")
    def agent_heartbeat(agent_id: str, hb: AgentHeartbeat):
        hb.agent_id = agent_id
        if not service.heartbeat(hb):
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "success"}

    @router.get("/agents", response_model=AgentListResponse)
    def list_agents(healthy_only: bool = False, hotpath_only: bool = False):
        return service.list_agents(healthy_only=healthy_only, hotpath_only=hotpath_only)

    @router.get("/agents/hotpath", response_model=AgentListResponse)
    def list_hotpath_agents():
        return service.list_agents(healthy_only=False, hotpath_only=True)

    @router.get("/agents/{agent_id}", response_model=AgentInfo)
    def get_agent(agent_id: str):
        agent = service.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    @router.delete("/agents/{agent_id}")
    def unregister_agent(agent_id: str, force: bool = False):
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
        try:
            return service.set_hotpath(agent_id, request.hotpath)
        except ValueError as exc:
            if str(exc) == "agent_not_found":
                raise HTTPException(status_code=404, detail="Agent not found")
            raise

    @router.get("/stats", response_model=RegistryStats)
    def get_stats():
        return service.stats()

    return router

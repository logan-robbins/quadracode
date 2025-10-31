from fastapi import APIRouter, HTTPException

from .schemas import AgentHeartbeat, AgentInfo, AgentListResponse, AgentRegistrationRequest, RegistryStats
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
    def list_agents(healthy_only: bool = False):
        return service.list_agents(healthy_only=healthy_only)

    @router.get("/agents/{agent_id}", response_model=AgentInfo)
    def get_agent(agent_id: str):
        agent = service.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    @router.delete("/agents/{agent_id}")
    def unregister_agent(agent_id: str):
        if not service.remove_agent(agent_id):
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"status": "success"}

    @router.get("/stats", response_model=RegistryStats)
    def get_stats():
        return service.stats()

    return router

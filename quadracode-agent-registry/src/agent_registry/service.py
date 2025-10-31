from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from .config import RegistrySettings
from .database import Database
from .schemas import (
    AgentHeartbeat,
    AgentInfo,
    AgentListResponse,
    AgentRegistrationRequest,
    AgentStatus,
    RegistryStats,
)


class AgentRegistryService:
    def __init__(self, db: Database, settings: RegistrySettings):
        self.db = db
        self.settings = settings

    def register(self, payload: AgentRegistrationRequest) -> AgentInfo:
        now = datetime.utcnow()
        self.db.upsert_agent(agent_id=payload.agent_id, host=payload.host, port=payload.port, now=now)
        return AgentInfo(
            agent_id=payload.agent_id,
            host=payload.host,
            port=payload.port,
            status=AgentStatus.HEALTHY,
            registered_at=now,
            last_heartbeat=now,
        )

    def heartbeat(self, hb: AgentHeartbeat) -> bool:
        return self.db.update_heartbeat(agent_id=hb.agent_id, status=hb.status.value, at=hb.reported_at)

    def _row_to_agent(self, row) -> AgentInfo:
        # Handle possible NULL for last_heartbeat
        last_hb = None
        if row["last_heartbeat"]:
            last_hb = datetime.fromisoformat(row["last_heartbeat"])  # type: ignore[arg-type]

        return AgentInfo(
            agent_id=row["agent_id"],
            host=row["host"],
            port=row["port"],
            status=AgentStatus(row["status"]),
            registered_at=datetime.fromisoformat(row["registered_at"]),
            last_heartbeat=last_hb,
        )

    def _is_healthy(self, agent: AgentInfo) -> bool:
        if agent.status != AgentStatus.HEALTHY:
            return False
        if agent.last_heartbeat is None:
            return False
        cutoff = datetime.utcnow() - timedelta(seconds=self.settings.agent_timeout)
        return agent.last_heartbeat >= cutoff

    def list_agents(self, healthy_only: bool = False) -> AgentListResponse:
        rows = self.db.fetch_agents()
        agents = [self._row_to_agent(r) for r in rows]
        if healthy_only:
            agents = [a for a in agents if self._is_healthy(a)]
        return AgentListResponse(agents=agents, healthy_only=healthy_only)

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        row = self.db.fetch_agent(agent_id=agent_id)
        if not row:
            return None
        return self._row_to_agent(row)

    def remove_agent(self, agent_id: str) -> bool:
        return self.db.delete_agent(agent_id=agent_id)

    def stats(self) -> RegistryStats:
        rows = self.db.fetch_agents()
        agents = [self._row_to_agent(r) for r in rows]
        healthy = sum(1 for a in agents if self._is_healthy(a))
        total = len(agents)
        return RegistryStats(
            total_agents=total,
            healthy_agents=healthy,
            unhealthy_agents=total - healthy,
        )


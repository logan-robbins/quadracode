"""Business logic for the Quadracode Agent Registry.

The ``AgentRegistryService`` sits between the API layer and the database,
owning all domain rules: registration, heartbeat processing, health
classification, hotpath protection, and statistics aggregation.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

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

logger = logging.getLogger(__name__)


class AgentRegistryService:
    """Encapsulates all agent-management business logic.

    Attributes:
        db: SQLite data-access layer.
        settings: Validated service configuration.
    """

    def __init__(self, db: Database, settings: RegistrySettings) -> None:
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def register(self, payload: AgentRegistrationRequest) -> AgentInfo:
        """Register a new agent or re-register an existing one.

        Args:
            payload: Registration request with agent identity and location.

        Returns:
            Full ``AgentInfo`` with server-assigned timestamps.
        """
        now = datetime.now(timezone.utc)
        self.db.upsert_agent(
            agent_id=payload.agent_id,
            host=payload.host,
            port=payload.port,
            now=now,
            hotpath=payload.hotpath,
        )
        logger.info(
            "Agent registered: %s at %s:%d (hotpath=%s)",
            payload.agent_id,
            payload.host,
            payload.port,
            payload.hotpath,
        )
        return AgentInfo(
            agent_id=payload.agent_id,
            host=payload.host,
            port=payload.port,
            status=AgentStatus.HEALTHY,
            registered_at=now,
            last_heartbeat=now,
            hotpath=payload.hotpath,
        )

    def heartbeat(self, hb: AgentHeartbeat) -> bool:
        """Process an agent heartbeat.

        Args:
            hb: Heartbeat payload with status and optional metrics.

        Returns:
            ``True`` if the heartbeat was recorded, ``False`` if agent unknown.
        """
        metrics_json = json.dumps(hb.metrics) if hb.metrics else None
        return self.db.update_heartbeat(
            agent_id=hb.agent_id,
            status=hb.status.value,
            at=hb.reported_at,
            metrics=metrics_json,
        )

    def list_agents(
        self,
        healthy_only: bool = False,
        hotpath_only: bool = False,
    ) -> AgentListResponse:
        """List agents with optional health or hotpath filtering.

        Args:
            healthy_only: When ``True``, exclude agents that have timed out.
            hotpath_only: When ``True``, only return hotpath-pinned agents.

        Returns:
            Response envelope containing the filtered agent list.
        """
        rows = self.db.fetch_agents(hotpath_only=hotpath_only)
        agents = [self._row_to_agent(r) for r in rows]
        if healthy_only:
            agents = [a for a in agents if self._is_healthy(a)]
        return AgentListResponse(
            agents=agents,
            healthy_only=healthy_only,
            hotpath_only=hotpath_only,
        )

    def get_agent(self, agent_id: str) -> AgentInfo | None:
        """Retrieve a single agent by ID.

        Returns:
            ``AgentInfo`` or ``None`` if not found.
        """
        row = self.db.fetch_agent(agent_id=agent_id)
        if not row:
            return None
        return self._row_to_agent(row)

    def remove_agent(self, agent_id: str, *, force: bool = False) -> bool:
        """Remove an agent from the registry.

        Hotpath agents cannot be removed unless *force* is ``True``.

        Args:
            agent_id: ID of the agent to remove.
            force: Bypass hotpath protection.

        Returns:
            ``True`` if an agent was deleted.

        Raises:
            ValueError: If the agent is hotpath-pinned and *force* is ``False``.
        """
        current = self.get_agent(agent_id)
        if current and current.hotpath and not force:
            raise ValueError("hotpath_agent")
        deleted = self.db.delete_agent(agent_id=agent_id)
        if deleted:
            logger.info("Agent removed: %s (force=%s)", agent_id, force)
        return deleted

    def set_hotpath(self, agent_id: str, hotpath: bool) -> AgentInfo:
        """Set or clear the hotpath flag for an agent.

        Args:
            agent_id: ID of the agent to modify.
            hotpath: Desired hotpath state.

        Returns:
            Updated ``AgentInfo``.

        Raises:
            ValueError: If the agent does not exist.
        """
        if not self.db.set_hotpath(agent_id=agent_id, hotpath=hotpath):
            raise ValueError("agent_not_found")
        agent = self.get_agent(agent_id)
        if not agent:
            raise ValueError("agent_not_found")
        logger.info("Agent %s hotpath set to %s", agent_id, hotpath)
        return agent

    def stats(self) -> RegistryStats:
        """Compute aggregate registry statistics.

        Returns:
            Snapshot of total, healthy, and unhealthy agent counts.
        """
        rows = self.db.fetch_agents()
        agents = [self._row_to_agent(r) for r in rows]
        healthy = sum(1 for a in agents if self._is_healthy(a))
        total = len(agents)
        return RegistryStats(
            total_agents=total,
            healthy_agents=healthy,
            unhealthy_agents=total - healthy,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_agent(self, row: object) -> AgentInfo:
        """Convert a ``sqlite3.Row`` into an ``AgentInfo`` model.

        Args:
            row: Database row with dict-style column access.

        Returns:
            Populated ``AgentInfo`` instance.
        """
        last_hb: datetime | None = None
        if row["last_heartbeat"]:  # type: ignore[index]
            last_hb = datetime.fromisoformat(row["last_heartbeat"])  # type: ignore[index]

        metrics: dict | None = None
        if "metrics" in row.keys() and row["metrics"]:  # type: ignore[union-attr]
            try:
                metrics = json.loads(row["metrics"])  # type: ignore[index]
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Malformed metrics JSON for agent %s, ignoring",
                    row["agent_id"],  # type: ignore[index]
                )

        return AgentInfo(
            agent_id=row["agent_id"],  # type: ignore[index]
            host=row["host"],  # type: ignore[index]
            port=row["port"],  # type: ignore[index]
            status=AgentStatus(row["status"]),  # type: ignore[index]
            registered_at=datetime.fromisoformat(row["registered_at"]),  # type: ignore[index]
            last_heartbeat=last_hb,
            hotpath=bool(row["hotpath"]),  # type: ignore[index]
            metrics=metrics,
        )

    @staticmethod
    def _to_utc(dt: datetime | None) -> datetime | None:
        """Ensure *dt* is timezone-aware in UTC."""
        if dt is None:
            return None
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _is_healthy(self, agent: AgentInfo) -> bool:
        """Return ``True`` if *agent* is healthy and has a recent heartbeat."""
        if agent.status != AgentStatus.HEALTHY:
            return False
        heartbeat = self._to_utc(agent.last_heartbeat)
        if heartbeat is None:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self.settings.agent_timeout
        )
        return heartbeat >= cutoff

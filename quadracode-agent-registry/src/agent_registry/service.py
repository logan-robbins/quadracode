"""
This module encapsulates the core business logic for the Quadracode Agent 
Registry.

The `AgentRegistryService` class orchestrates all agent management operations, 
acting as an intermediary between the API layer and the database. It handles the 
logic for registration, health monitoring (heartbeats), and agent lifecycle 
management. By separating the business logic into this service layer, the API 
endpoints remain lightweight and focused on handling HTTP requests, while the 
service class manages the more complex state transitions and data validation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    """
    Provides the business logic for managing agent registrations.

    This service class contains all the methods necessary to handle the 
    registration, health tracking, and lifecycle of agents. It is initialized 
    with a database connection and the application settings, which it uses to 
    persist agent data and enforce health policies.

    Attributes:
        db: An instance of the `Database` class for data access.
        settings: An instance of `RegistrySettings` for configuration.
    """

    def __init__(self, db: Database, settings: RegistrySettings):
        """
        Initializes the AgentRegistryService.

        Args:
            db: The database access layer.
            settings: The application configuration settings.
        """
        self.db = db
        self.settings = settings

    def register(self, payload: AgentRegistrationRequest) -> AgentInfo:
        """
        Registers a new agent or updates an existing one.

        This method takes a registration payload, persists it to the database 
        using an upsert operation, and returns the complete agent information.

        Args:
            payload: The registration request from the agent.

        Returns:
            The full agent information, including server-generated timestamps.
        """
        now = datetime.utcnow()
        self.db.upsert_agent(
            agent_id=payload.agent_id,
            host=payload.host,
            port=payload.port,
            now=now,
            hotpath=payload.hotpath,
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
        """
        Processes a heartbeat from an agent.

        This method updates the agent's last heartbeat timestamp and status in 
        the database.

        Args:
            hb: The heartbeat payload from the agent.

        Returns:
            True if the heartbeat was successfully recorded, False otherwise.
        """
        return self.db.update_heartbeat(agent_id=hb.agent_id, status=hb.status.value, at=hb.reported_at)

    def _row_to_agent(self, row) -> AgentInfo:
        """
        Converts a database row into an `AgentInfo` Pydantic model.

        This private helper method handles the transformation from a `sqlite3.Row` 
        object to a strongly-typed `AgentInfo` model, including parsing date/time 
        strings.

        Args:
            row: The `sqlite3.Row` object from the database.

        Returns:
            An `AgentInfo` model instance.
        """
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
            hotpath=bool(row["hotpath"]),
        )

    @staticmethod
    def _to_utc(dt: datetime | None) -> datetime | None:
        """
        Converts a naive datetime object to a timezone-aware UTC datetime.

        This static helper ensures that all datetime comparisons are done in a 
        consistent timezone (UTC).

        Args:
            dt: The datetime object to convert.

        Returns:
            A timezone-aware datetime object, or None if the input was None.
        """
        if dt is None:
            return None
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _is_healthy(self, agent: AgentInfo) -> bool:
        """
        Determines if an agent is currently healthy.

        An agent is considered healthy if its status is 'healthy' and its last 
        heartbeat was received within the configured timeout period.

        Args:
            agent: The agent to check.

        Returns:
            True if the agent is healthy, False otherwise.
        """
        if agent.status != AgentStatus.HEALTHY:
            return False
        heartbeat = self._to_utc(agent.last_heartbeat)
        if heartbeat is None:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.settings.agent_timeout)
        return heartbeat >= cutoff

    def list_agents(self, healthy_only: bool = False, hotpath_only: bool = False) -> AgentListResponse:
        """
        Lists all registered agents, with optional filtering.

        This method retrieves all agents from the database and can filter them 
        based on health status or hotpath assignment.

        Args:
            healthy_only: If True, only returns healthy agents.
            hotpath_only: If True, only returns agents on the hotpath.

        Returns:
            A response object containing the list of agents and filter metadata.
        """
        rows = self.db.fetch_agents(hotpath_only=hotpath_only)
        agents = [self._row_to_agent(r) for r in rows]
        if healthy_only:
            agents = [a for a in agents if self._is_healthy(a)]
        return AgentListResponse(agents=agents, healthy_only=healthy_only, hotpath_only=hotpath_only)

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """
        Retrieves a single agent by its ID.

        Args:
            agent_id: The ID of the agent to retrieve.

        Returns:
            The agent's information, or None if the agent is not found.
        """
        row = self.db.fetch_agent(agent_id=agent_id)
        if not row:
            return None
        return self._row_to_agent(row)

    def remove_agent(self, agent_id: str, *, force: bool = False) -> bool:
        """
        Removes an agent from the registry.

        This method includes a safety check to prevent the accidental removal 
        of a hotpath agent, unless the `force` flag is set.

        Args:
            agent_id: The ID of the agent to remove.
            force: If True, allows the removal of a hotpath agent.

        Returns:
            True if the agent was successfully removed, False otherwise.
        """
        current = self.get_agent(agent_id)
        if current and current.hotpath and not force:
            raise ValueError("hotpath_agent")
        return self.db.delete_agent(agent_id=agent_id)

    def set_hotpath(self, agent_id: str, hotpath: bool) -> AgentInfo:
        """
        Sets the hotpath status for an agent.

        Args:
            agent_id: The ID of the agent to modify.
            hotpath: The new hotpath status.

        Returns:
            The updated agent information.
        """
        updated = self.db.set_hotpath(agent_id=agent_id, hotpath=hotpath)
        if not updated:
            raise ValueError("agent_not_found")
        agent = self.get_agent(agent_id)
        if not agent:
            raise ValueError("agent_not_found")
        return agent

    def stats(self) -> RegistryStats:
        """
        Calculates and returns statistics about the registry.

        This method provides a summary of the registry's state, including the 
        total number of agents and their health distribution.

        Returns:
            A `RegistryStats` object with the current statistics.
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

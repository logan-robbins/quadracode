"""Tests for agent_registry module."""
from datetime import datetime, timezone
import pytest
from pydantic import ValidationError

from quadracode_contracts.agent_registry import (
    AgentStatus,
    AgentRegistrationRequest,
    AgentHeartbeat,
    AgentInfo,
    AgentListResponse,
    RegistryStats,
)


class TestAgentStatus:
    """Tests for AgentStatus enum."""

    def test_enum_values(self):
        """Should have expected status values."""
        assert AgentStatus.HEALTHY == "healthy"
        assert AgentStatus.UNHEALTHY == "unhealthy"

    def test_enum_from_string(self):
        """Should be constructible from string values."""
        assert AgentStatus("healthy") == AgentStatus.HEALTHY
        assert AgentStatus("unhealthy") == AgentStatus.UNHEALTHY


class TestAgentRegistrationRequest:
    """Tests for AgentRegistrationRequest model."""

    def test_valid_registration(self):
        """Should accept valid registration data."""
        request = AgentRegistrationRequest(
            agent_id="agent-abc12345",
            host="192.168.1.100",
            port=8080
        )
        assert request.agent_id == "agent-abc12345"
        assert request.host == "192.168.1.100"
        assert request.port == 8080

    def test_hostname_registration(self):
        """Should accept hostname instead of IP."""
        request = AgentRegistrationRequest(
            agent_id="agent-worker-01",
            host="worker-01.quadracode.local",
            port=9090
        )
        assert request.host == "worker-01.quadracode.local"

    def test_missing_required_field(self):
        """Should reject registration with missing required fields."""
        with pytest.raises(ValidationError):
            AgentRegistrationRequest(agent_id="agent-123", host="localhost")


class TestAgentHeartbeat:
    """Tests for AgentHeartbeat model."""

    def test_default_status_is_healthy(self):
        """Default status should be HEALTHY."""
        heartbeat = AgentHeartbeat(agent_id="agent-abc12345")
        assert heartbeat.status == AgentStatus.HEALTHY

    def test_reported_at_auto_populated(self):
        """reported_at should be auto-populated with current time."""
        heartbeat = AgentHeartbeat(agent_id="agent-abc12345")
        assert heartbeat.reported_at is not None
        assert isinstance(heartbeat.reported_at, datetime)

    def test_explicit_status(self):
        """Should accept explicit status."""
        heartbeat = AgentHeartbeat(
            agent_id="agent-abc12345",
            status=AgentStatus.UNHEALTHY
        )
        assert heartbeat.status == AgentStatus.UNHEALTHY


class TestAgentInfo:
    """Tests for AgentInfo model."""

    def test_full_agent_info(self):
        """Should accept complete agent info."""
        now = datetime.now(timezone.utc)
        info = AgentInfo(
            agent_id="agent-prod-01",
            host="10.0.0.50",
            port=8080,
            status=AgentStatus.HEALTHY,
            registered_at=now,
            last_heartbeat=now
        )
        assert info.agent_id == "agent-prod-01"
        assert info.status == AgentStatus.HEALTHY
        assert info.last_heartbeat == now

    def test_optional_heartbeat(self):
        """last_heartbeat should be optional."""
        now = datetime.now(timezone.utc)
        info = AgentInfo(
            agent_id="agent-new",
            host="localhost",
            port=8080,
            status=AgentStatus.HEALTHY,
            registered_at=now
        )
        assert info.last_heartbeat is None


class TestAgentListResponse:
    """Tests for AgentListResponse model."""

    def test_empty_list(self):
        """Should accept empty agent list."""
        response = AgentListResponse(agents=[])
        assert response.agents == []
        assert response.healthy_only is False

    def test_with_agents(self):
        """Should accept list of agents."""
        now = datetime.now(timezone.utc)
        agents = [
            AgentInfo(
                agent_id="agent-01",
                host="10.0.0.1",
                port=8080,
                status=AgentStatus.HEALTHY,
                registered_at=now
            ),
            AgentInfo(
                agent_id="agent-02",
                host="10.0.0.2",
                port=8081,
                status=AgentStatus.UNHEALTHY,
                registered_at=now
            ),
        ]
        response = AgentListResponse(agents=agents, healthy_only=True)
        assert len(response.agents) == 2
        assert response.healthy_only is True


class TestRegistryStats:
    """Tests for RegistryStats model."""

    def test_stats_creation(self):
        """Should create valid stats object."""
        stats = RegistryStats(
            total_agents=10,
            healthy_agents=8,
            unhealthy_agents=2
        )
        assert stats.total_agents == 10
        assert stats.healthy_agents == 8
        assert stats.unhealthy_agents == 2
        assert stats.last_updated is not None

    def test_zero_agents(self):
        """Should handle zero agents."""
        stats = RegistryStats(
            total_agents=0,
            healthy_agents=0,
            unhealthy_agents=0
        )
        assert stats.total_agents == 0

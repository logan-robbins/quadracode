"""Tests for hotpath agent management in the Agent Registry service."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_registry.config import RegistrySettings
from agent_registry.database import Database
from agent_registry.schemas import AgentRegistrationRequest
from agent_registry.service import AgentRegistryService


def _make_service(db_path: Path) -> AgentRegistryService:
    """Build an isolated service instance backed by a temporary SQLite file."""
    settings = RegistrySettings(database_path=str(db_path))
    database = Database(settings.database_path)
    database.init_schema()
    return AgentRegistryService(db=database, settings=settings)


def test_hotpath_toggle_and_removal_guard(tmp_path: Path) -> None:
    """Hotpath agents are protected from removal unless force=True."""
    service = _make_service(tmp_path / "registry.db")
    service.register(
        AgentRegistrationRequest(agent_id="alpha", host="localhost", port=8123)
    )

    updated = service.set_hotpath("alpha", True)
    assert updated.hotpath is True

    with pytest.raises(ValueError):
        service.remove_agent("alpha")

    service.remove_agent("alpha", force=True)
    assert service.get_agent("alpha") is None


def test_hotpath_listing_returns_only_marked_agents(tmp_path: Path) -> None:
    """list_agents(hotpath_only=True) returns exactly the hotpath-pinned agents."""
    service = _make_service(tmp_path / "registry_alt.db")
    service.register(
        AgentRegistrationRequest(agent_id="alpha", host="localhost", port=8123)
    )
    service.register(
        AgentRegistrationRequest(agent_id="beta", host="localhost", port=8124)
    )
    service.set_hotpath("beta", True)

    hotpath_only = service.list_agents(hotpath_only=True)
    assert len(hotpath_only.agents) == 1
    assert hotpath_only.agents[0].agent_id == "beta"
    assert hotpath_only.agents[0].hotpath is True


def test_register_returns_agent_info(tmp_path: Path) -> None:
    """Registration returns a fully populated AgentInfo."""
    service = _make_service(tmp_path / "registry.db")
    info = service.register(
        AgentRegistrationRequest(agent_id="gamma", host="10.0.0.1", port=9090)
    )
    assert info.agent_id == "gamma"
    assert info.host == "10.0.0.1"
    assert info.port == 9090
    assert info.hotpath is False
    assert info.last_heartbeat is not None


def test_stats_counts(tmp_path: Path) -> None:
    """Stats correctly report total/healthy/unhealthy counts."""
    service = _make_service(tmp_path / "registry.db")
    service.register(
        AgentRegistrationRequest(agent_id="a1", host="localhost", port=8001)
    )
    service.register(
        AgentRegistrationRequest(agent_id="a2", host="localhost", port=8002)
    )
    st = service.stats()
    assert st.total_agents == 2
    assert st.healthy_agents == 2
    assert st.unhealthy_agents == 0

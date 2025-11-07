from __future__ import annotations

from pathlib import Path

import pytest

from agent_registry.config import RegistrySettings
from agent_registry.database import Database
from agent_registry.schemas import AgentRegistrationRequest
from agent_registry.service import AgentRegistryService


def _make_service(db_path: Path) -> AgentRegistryService:
    settings = RegistrySettings(database_path=str(db_path))
    database = Database(settings.database_path)
    database.init_schema()
    return AgentRegistryService(db=database, settings=settings)


def test_hotpath_toggle_and_removal_guard(tmp_path: Path) -> None:
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

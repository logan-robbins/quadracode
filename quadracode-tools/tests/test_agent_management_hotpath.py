from __future__ import annotations

import json

from quadracode_tools.tools import agent_management as tool_module


def test_delete_agent_blocks_hotpath(monkeypatch):
    monkeypatch.setattr(tool_module, "_is_hotpath_agent", lambda agent_id: True)

    output = tool_module.agent_management_tool.func(operation="delete_agent", agent_id="critical")
    payload = json.loads(output)

    assert payload["success"] is False
    assert payload["error"] == "hotpath_agent"


def test_mark_hotpath_delegates_to_registry(monkeypatch):
    captured = {}

    def fake_update(agent_id, hotpath):
        captured["agent_id"] = agent_id
        captured["hotpath"] = hotpath
        return {"success": True, "agent": {"agent_id": agent_id, "hotpath": hotpath}}

    monkeypatch.setattr(tool_module, "_update_hotpath_flag", fake_update)

    output = tool_module.agent_management_tool.func(operation="mark_hotpath", agent_id="beta")
    payload = json.loads(output)

    assert payload["success"] is True
    assert captured == {"agent_id": "beta", "hotpath": True}


def test_list_hotpath_uses_registry(monkeypatch):
    monkeypatch.setattr(
        tool_module,
        "_list_hotpath_agents",
        lambda: {"success": True, "agents": [{"agent_id": "hot", "status": "healthy"}]},
    )

    output = tool_module.agent_management_tool.func(operation="list_hotpath")
    payload = json.loads(output)

    assert payload["success"] is True
    assert payload["agents"][0]["agent_id"] == "hot"

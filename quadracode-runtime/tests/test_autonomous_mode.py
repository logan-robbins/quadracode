from __future__ import annotations

import os
import sys
import types
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import pytest

os.environ.setdefault("SHARED_PATH", "/tmp")
os.environ.setdefault("PERPLEXITY_API_KEY", "dummy-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-key")

_mcp_stub = types.ModuleType("quadracode_runtime.tools.mcp_loader")
_mcp_stub.load_mcp_tools_sync = lambda: []

async def _aget_mcp_tools_stub():  # pragma: no cover - simple coroutine stub
    return []

_mcp_stub.aget_mcp_tools = _aget_mcp_tools_stub
sys.modules.setdefault("quadracode_runtime.tools.mcp_loader", _mcp_stub)

from quadracode_contracts import (
    HUMAN_RECIPIENT,
    MessageEnvelope,
    ORCHESTRATOR_RECIPIENT,
)
from langchain_core.messages import AIMessage

from quadracode_runtime import profiles as profiles_module
from quadracode_runtime.runtime import (
    RuntimeRunner,
    _apply_autonomous_limits,
)
from quadracode_runtime.state import ExhaustionMode


def _make_envelope(sender: str, recipient: str, message: str = "msg") -> MessageEnvelope:
    return MessageEnvelope(
        sender=sender,
        recipient=recipient,
        message=message,
        payload={},
    )


def test_autonomous_policy_excludes_human(monkeypatch):
    monkeypatch.setenv("QUADRACODE_MODE", "autonomous")
    profile = profiles_module.load_profile("orchestrator")

    payload = {"reply_to": ["agent-1"]}
    envelope = _make_envelope(ORCHESTRATOR_RECIPIENT, "agent-1")

    recipients = profile.resolve_recipients(envelope, payload)

    assert "agent-1" in recipients
    assert HUMAN_RECIPIENT not in recipients


def test_autonomous_policy_allows_human_when_directive(monkeypatch):
    monkeypatch.setenv("QUADRACODE_MODE", "autonomous")
    profile = profiles_module.load_profile("orchestrator")

    payload = {
        "reply_to": ["agent-1"],
        "autonomous": {"deliver_to_human": True},
    }
    envelope = _make_envelope(ORCHESTRATOR_RECIPIENT, "agent-1")

    recipients = profile.resolve_recipients(envelope, payload)

    assert HUMAN_RECIPIENT in recipients


@pytest.mark.parametrize(
    "setting_key, state_value, expected_event",
    [
        ("max_iterations", 5, "iteration_limit"),
        ("max_hours", 1.0, "runtime_limit"),
    ],
)
def test_autonomous_guardrail_events(monkeypatch, setting_key, state_value, expected_event):
    events: List[Tuple[str, dict, List[str] | None]] = []

    def _capture(event: str, payload: dict, *, categories: List[str] | None = None) -> None:
        events.append((event, payload, categories))

    monkeypatch.setattr("quadracode_runtime.runtime._publish_autonomous_event", _capture)

    state = {
        "autonomous_mode": True,
        "iteration_count": 5,
        "autonomous_settings": {setting_key: state_value},
        "thread_id": "thread-1",
        "error_history": [],
    }
    result = {
        "autonomous_mode": True,
        "iteration_count": 5,
        "autonomous_settings": deepcopy(state["autonomous_settings"]),
        "thread_id": "thread-1",
    }

    if setting_key == "max_hours":
        started_at = (datetime.now(timezone.utc) - timedelta(hours=state_value + 1)).isoformat()
        state["autonomous_started_at"] = started_at
        result["autonomous_started_at"] = started_at

    _apply_autonomous_limits(state, result)

    assert events, "Expected guardrail event to be emitted"
    emitted_event, payload, categories = events[-1]
    assert emitted_event == "guardrail_trigger"
    assert expected_event in payload["type"]
    assert "guardrail" in (categories or [])
    assert result.get("autonomous_routing", {}).get("escalate") is True


def test_emergency_stop_routes_to_human(monkeypatch):
    captured_events: List[Tuple[str, dict, List[str] | None]] = []

    def _capture(event: str, payload: dict, *, categories: List[str] | None = None) -> None:
        captured_events.append((event, payload, categories))

    monkeypatch.setattr("quadracode_runtime.runtime._publish_autonomous_event", _capture)

    monkeypatch.setenv("QUADRACODE_MODE", "autonomous")
    runner = object.__new__(RuntimeRunner)
    runner._identity = ORCHESTRATOR_RECIPIENT  # type: ignore[attr-defined]
    runner._profile = profiles_module.load_profile("orchestrator")  # type: ignore[attr-defined]

    state = {
        "autonomous_mode": True,
        "error_history": [],
        "iteration_count": 2,
        "thread_id": "thread-stop",
    }
    payload = {"autonomous_control": {"action": "emergency_stop"}}
    envelope = _make_envelope(HUMAN_RECIPIENT, ORCHESTRATOR_RECIPIENT, "stop")

    responses = runner._handle_emergency_stop(envelope, payload, state, "thread-stop")

    assert len(responses) >= 1
    response_payload = responses[0].payload
    assert response_payload["autonomous"]["escalate"] is True
    assert response_payload["state"]["current_phase"] == "halted_by_human"
    assert captured_events[-1][0] == "control_event"


def test_runtime_runner_autonomous_loop(monkeypatch):
    monkeypatch.setenv("QUADRACODE_MODE", "autonomous")

    events: List[Tuple[str, dict, List[str] | None]] = []
    monkeypatch.setattr(
        "quadracode_runtime.runtime._publish_autonomous_event",
        lambda event, payload, categories=None: events.append((event, payload, categories)),
    )

    runner = RuntimeRunner(profiles_module.load_profile("orchestrator"))

    class StubGraph:
        def __init__(self) -> None:
            self.counter = 0

        def invoke(self, state, config):
            self.counter += 1
            result = {
                "messages": [AIMessage(content=f"iteration-{self.counter}")],
                "iteration_count": self.counter,
                "autonomous_mode": True,
                "milestones": [
                    {
                        "milestone": 1,
                        "status": "in_progress",
                        "summary": f"stub-step-{self.counter}",
                        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                ],
                "error_history": state.get("error_history", []),
            }
            if self.counter == 3:
                result["autonomous_routing"] = {
                    "deliver_to_human": True,
                    "escalate": True,
                    "reason": "stub complete",
                    "recovery_attempts": [],
                }
            return result

    runner._graph = StubGraph()  # type: ignore[attr-defined]

    start_envelope = MessageEnvelope(
        sender=HUMAN_RECIPIENT,
        recipient=ORCHESTRATOR_RECIPIENT,
        message="Start autonomous task",
        payload={
            "chat_id": "loop-chat",
            "reply_to": ["agent-loop"],
            "autonomous_settings": {"max_iterations": 5},
        },
    )

    first_responses = asyncio.run(runner._process_envelope(start_envelope))
    assert first_responses
    assert all(resp.recipient != HUMAN_RECIPIENT for resp in first_responses)
    assert all(
        resp.payload.get("exhaustion_mode") == ExhaustionMode.NONE.value
        for resp in first_responses
    )
    assert all(
        "exhaustion_probability" in resp.payload
        for resp in first_responses
    )

    agent_msg = MessageEnvelope(
        sender="agent-loop",
        recipient=ORCHESTRATOR_RECIPIENT,
        message="progress update",
        payload={
            "chat_id": "loop-chat",
            "reply_to": ["agent-loop"],
        },
    )

    asyncio.run(runner._process_envelope(agent_msg))
    final_responses = asyncio.run(runner._process_envelope(agent_msg))

    assert final_responses
    recipients = [resp.recipient for resp in final_responses]
    assert HUMAN_RECIPIENT in recipients
    assert all("exhaustion_mode" in resp.payload for resp in final_responses)
    assert any(event for event, payload, _ in events if event == "guardrail_trigger") is False
    assert any(
        payload.get("reason") == "stub complete"
        for resp in final_responses
        for payload in [resp.payload.get("autonomous")]
        if isinstance(payload, dict)
    )

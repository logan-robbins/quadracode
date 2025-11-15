"""Tests for autonomous mode UI components and state management."""
from __future__ import annotations

from types import SimpleNamespace

from quadracode_ui import app


class _StubStreamlit:
    """A stub for the `streamlit` module for isolated testing."""

    def __init__(self) -> None:
        self.session_state = {}

    def error(self, *args, **kwargs) -> None:  # pragma: no cover - test stub
        pass


def test_load_autonomous_events_parses_payload(monkeypatch):
    """
    Verifies that `_load_autonomous_events` correctly parses JSON payloads.

    This test ensures that the function deserializes the `payload` field from
    Redis stream entries into a dictionary, making it accessible for rendering
    in the UI. It uses a `DummyRedis` to provide controlled input.
    """
    stub = _StubStreamlit()
    monkeypatch.setattr(app, "st", stub)

    class DummyRedis:
        def xrevrange(self, key, count):  # pragma: no cover - deterministic
            return [
                ("2-0", {"event": "critique", "timestamp": "2025-01-01T00:00:02Z", "payload": '{"note":"c"}'}),
                ("1-0", {"event": "checkpoint", "timestamp": "2025-01-01T00:00:01Z", "payload": '{"note":"a"}'}),
            ]

    events = app._load_autonomous_events(DummyRedis(), limit=10)

    assert len(events) == 2
    assert events[0]["event"] == "checkpoint"
    assert events[0]["payload"]["note"] == "a"
    assert events[1]["event"] == "critique"


def test_current_autonomous_settings(monkeypatch):
    """
    Ensures `_current_autonomous_settings` correctly reads from session state.

    This test populates the `session_state` with mock autonomous mode settings
    and verifies that the `_current_autonomous_settings` function retrieves
    and formats them correctly into a dictionary.
    """
    stub = _StubStreamlit()
    stub.session_state.update(
        {
            "autonomous_max_iterations": 42,
            "autonomous_max_hours": 12.5,
            "autonomous_max_agents": 6,
        }
    )
    stub.session_state.update(
        {
            "autonomous_mode_enabled": True,
            "autonomous_chat_settings": {},
        }
    )

    monkeypatch.setattr(app, "st", stub)

    settings = app._current_autonomous_settings()

    assert settings == {"max_iterations": 42, "max_hours": 12.5, "max_agents": 6}

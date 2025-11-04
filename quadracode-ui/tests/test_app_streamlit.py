from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

import pytest
from streamlit.testing.v1 import AppTest

from quadracode_contracts import MessageEnvelope
from quadracode_ui.app import (
    HUMAN_RECIPIENT,
    MAILBOX_HUMAN,
    MAILBOX_ORCHESTRATOR,
    ORCHESTRATOR_RECIPIENT,
)


def run_app() -> None:
    """Entry point executed by Streamlit's AppTest harness."""
    # Import inside the function so the generated script has access to the module.
    import quadracode_ui.app as app_module

    app_module.main()


class FakeRedis:
    """Minimal Redis stub to drive the Streamlit app during tests."""

    def __init__(self, metrics_stream: str) -> None:
        self._delivered = False
        self.stream_entries: List[Tuple[str, Dict[str, str]]] = []
        self.xadd_envelopes: List[Tuple[str, MessageEnvelope]] = []
        self.metrics_stream = metrics_stream
        self.metrics_entries: List[Tuple[str, Dict[str, str]]] = []
        self.workspace_descriptor: Dict[str, Any] | None = None
        self.workspace_events: List[Tuple[str, Dict[str, str]]] = []
        self._populate_metrics()

    def ping(self) -> None:
        return

    def scan_iter(self, pattern: str) -> Iterable[str]:
        yield MAILBOX_HUMAN

    def _build_entry(
        self,
        *,
        chat_id: str | None,
        message: str,
        ticket_id: str,
        entry_id: str,
    ) -> Tuple[str, Dict[str, str]]:
        payload: Dict[str, Any] = {"chat_id": chat_id, "ticket_id": ticket_id, "messages": []}
        if self.workspace_descriptor:
            payload["workspace"] = self.workspace_descriptor
        envelope = MessageEnvelope(
            sender=ORCHESTRATOR_RECIPIENT,
            recipient=HUMAN_RECIPIENT,
            message=message,
            payload=payload,
        )
        return entry_id, envelope.to_stream_fields()

    def xrevrange(self, key: str, count: int = 1) -> List[Tuple[str, Dict[str, str]]]:
        if key != MAILBOX_HUMAN:
            if key == self.metrics_stream:
                return list(reversed(self.metrics_entries))[:count]
            if key.startswith("qc:workspace:"):
                return self.workspace_events[:count]
            return []

        from streamlit import session_state as ss

        chat_id = ss.get("chat_id")
        if not self.stream_entries:
            self.stream_entries.append(
                self._build_entry(
                    chat_id=chat_id,
                    message="baseline",
                    ticket_id="baseline-ticket",
                    entry_id="1-0",
                )
            )
        return self.stream_entries[:count]

    def xread(
        self,
        streams: Dict[str, str],
        block: int | None = None,
        count: int | None = None,
    ) -> List[Tuple[str, List[Tuple[str, Dict[str, str]]]]]:
        if self._delivered:
            return []

        from streamlit import session_state as ss

        chat_id = ss.get("chat_id")
        entry = self._build_entry(
            chat_id=chat_id,
            message="response message",
            ticket_id="ticket-1",
            entry_id="2-0",
        )
        # Prepend so the stream viewer surfaces the most recent entry.
        self.stream_entries.insert(0, entry)
        self._delivered = True
        return [(MAILBOX_HUMAN, [entry])]

    def xadd(self, key: str, fields: Dict[str, str]) -> None:
        self.xadd_envelopes.append((key, MessageEnvelope.from_stream_fields(fields)))

    def _populate_metrics(self) -> None:
        now = datetime.now(timezone.utc)
        sample = [
            (
                "1-0",
                {
                    "event": "pre_process",
                    "timestamp": (now - timedelta(minutes=2)).isoformat(),
                    "payload": json.dumps(
                        {
                            "quality_score": 0.62,
                            "context_window_used": 4200,
                            "quality_components": {"relevance": 0.6},
                        }
                    ),
                },
            ),
            (
                "2-0",
                {
                    "event": "tool_response",
                    "timestamp": (now - timedelta(minutes=1)).isoformat(),
                    "payload": json.dumps({"operation": "summarize", "relevance": 0.45}),
                },
            ),
            (
                "2-5",
                {
                    "event": "curation",
                    "timestamp": (now - timedelta(seconds=45)).isoformat(),
                    "payload": json.dumps(
                        {
                            "operation_counts": {"externalize": 1, "summarize": 2},
                            "total_segments": 8,
                            "reason": "quality_recovery",
                        }
                    ),
                },
            ),
            (
                "2-8",
                {
                    "event": "load",
                    "timestamp": (now - timedelta(seconds=30)).isoformat(),
                    "payload": json.dumps(
                        {
                            "count": 3,
                            "segments": [
                                {"segment_id": "context-code-overview", "type": "code_context", "tokens": 120},
                                {"segment_id": "skill-debugging-playbook", "type": "skill:debugging-playbook", "tokens": 220},
                                {"segment_id": "context-error-history", "type": "error_history", "tokens": 80},
                            ],
                        }
                    ),
                },
            ),
            (
                "2-9",
                {
                    "event": "externalize",
                    "timestamp": (now - timedelta(seconds=20)).isoformat(),
                    "payload": json.dumps(
                        {
                            "count": 1,
                            "externalizations": [
                                {
                                    "id": "ext-123",
                                    "path": "/shared/context_memory/memory/seg-1-ext-123.json",
                                    "source_segment": "seg-1",
                                    "source_type": "memory",
                                }
                            ],
                        }
                    ),
                },
            ),
            (
                "3-0",
                {
                    "event": "post_process",
                    "timestamp": now.isoformat(),
                    "payload": json.dumps(
                        {
                            "quality_score": 0.74,
                            "focus_metric": "relevance",
                            "context_window_used": 4800,
                        }
                    ),
                },
            ),
            (
                "3-1",
                {
                    "event": "governor_plan",
                    "timestamp": (now + timedelta(seconds=5)).isoformat(),
                    "payload": json.dumps(
                        {
                            "action_counts": {"retain": 2, "summarize": 1},
                            "ordered_segments": ["skill-debugging-playbook", "context-code-overview"],
                            "focus": ["debugging", "error triage"],
                        }
                    ),
                },
            ),
        ]
        self.metrics_entries = sample


@pytest.fixture(autouse=True)
def _clear_cached_client() -> None:
    """Ensure cached Redis client state does not bleed between tests."""
    import quadracode_ui.app as app_module

    app_module.get_redis_client.clear()


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    import quadracode_ui.app as app_module

    stub = FakeRedis(app_module.CONTEXT_METRICS_STREAM)

    monkeypatch.setattr(app_module.redis, "Redis", lambda *_, **__: stub)
    monkeypatch.setattr(
        app_module,
        "_registry_snapshot",
        lambda: {"agents": [], "stats": None, "error": None},
    )
    monkeypatch.setattr(app_module, "_ensure_mailbox_watcher", lambda _client: None)
    return stub


def test_chat_fragment_pulls_responses(fake_redis: FakeRedis) -> None:
    tester = AppTest.from_function(run_app, default_timeout=3.0)
    tester.run()

    # The fake registry suppresses sidebar errors, so the chat tab should render cleanly.
    assert not tester.error
    assert tester.chat_message
    assert tester.chat_message[-1].markdown[0].value == "response message"


def test_chat_input_enqueues_messages(fake_redis: FakeRedis) -> None:
    tester = AppTest.from_function(run_app, default_timeout=3.0)
    tester.run()

    assert tester.chat_input
    chat_box = tester.chat_input[0]
    chat_box.set_value("Ping orchestrator")
    chat_box.run()

    assert fake_redis.xadd_envelopes, "Expected chat input to enqueue a Redis entry"
    key, envelope = fake_redis.xadd_envelopes[-1]
    assert key == MAILBOX_ORCHESTRATOR
    assert envelope.message == "Ping orchestrator"
    assert envelope.payload.get("chat_id") == tester.session_state["chat_id"]


def test_load_context_metrics_parses_extended_events(fake_redis: FakeRedis) -> None:
    import quadracode_ui.app as app_module

    entries = app_module._load_context_metrics(fake_redis, limit=20)
    events = {entry["event"] for entry in entries}
    assert {"pre_process", "tool_response", "curation", "load", "externalize", "post_process", "governor_plan"}.issubset(events)
    load_entries = [entry for entry in entries if entry["event"] == "load"]
    assert load_entries
    serialized = json.dumps(load_entries[0]["payload"])
    assert "skill:debugging-playbook" in serialized


def test_workspace_panel_renders_descriptor_and_events(fake_redis: FakeRedis) -> None:
    from quadracode_ui import app as app_module

    descriptor = {
        "workspace_id": "chat-workspace",
        "volume": "qc-ws-chat-workspace",
        "container": "qc-ws-chat-workspace-ctr",
        "mount_path": "/workspace",
        "image": "quadracode-workspace:latest",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fake_redis.workspace_descriptor = descriptor
    fake_redis.workspace_events = [
        (
            "3-0",
            {
                "event": "workspace_created",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": json.dumps({"message": "provisioned"}),
            },
        ),
        (
            "4-0",
            {
                "event": "command_executed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": json.dumps({"command": "pytest", "returncode": 0}),
            },
        ),
    ]

    tester = AppTest.from_function(run_app, default_timeout=3.0)
    tester.run()

    workspace_headers = [header.value for header in tester.sidebar.header]
    assert "Workspace" in workspace_headers

    # The workspace events table is the last dataframe rendered in the sidebar.
    assert tester.sidebar.dataframe
    events_df = tester.sidebar.dataframe[-1].value
    assert {"workspace_created", "command_executed"}.issubset(set(events_df["event"]))

    # Multiselect should offer both event types as filters.
    assert tester.sidebar.multiselect
    multiselect_widget = tester.sidebar.multiselect[-1]
    assert set(multiselect_widget.options) >= {"workspace_created", "command_executed"}

    # Stream caption should reference the workspace volume.
    stream_caption_values = [caption.value for caption in tester.sidebar.caption]
    assert any(descriptor["volume"] in text for text in stream_caption_values)

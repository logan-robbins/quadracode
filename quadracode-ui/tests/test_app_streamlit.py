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
        payload = {"chat_id": chat_id, "ticket_id": ticket_id, "messages": []}
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

"""
Integration tests for the Quadracode Streamlit application using a live Redis.

These tests verify the application's core functionality of sending and receiving
messages over Redis Streams. They are marked as 'integration' tests and require a
running Redis instance to be available. The tests simulate user input to send
a message and then check the orchestrator's mailbox. They also simulate the
orchestrator sending a message back and verify that the UI correctly displays it.
"""
from __future__ import annotations

import os
import time
from typing import Dict, Tuple

import pytest
import redis
from streamlit.testing.v1 import AppTest

from quadracode_contracts import HUMAN_RECIPIENT, ORCHESTRATOR_RECIPIENT, MessageEnvelope


REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))


def _live_redis() -> redis.Redis:
    """
    Establishes and returns a connection to a live Redis server.

    Pings the server to ensure connectivity. Fails if Redis is not available.
    """
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    r.ping()
    return r


@pytest.mark.integration
def test_live_send_writes_to_orchestrator_stream() -> None:
    """
    Runs the real app and verifies chat input writes to Redis Streams.

    This test uses a live Redis connection (no stubs). It simulates a user
    entering a message in the chat input and asserts that a corresponding new
    entry appears on the orchestrator's mailbox stream. It validates the
    payload of the message, including the active `chat_id` from the app's
    session state, to ensure correct message formation and delivery.
    """
    try:
        r = _live_redis()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Live Redis not available: {exc}")

    # Import after Redis check to avoid importing the app when Redis is missing
    import quadracode_ui.app as app_module

    tester = AppTest.from_file(app_module.__file__, default_timeout=2.0)
    tester.run()

    assert tester.chat_input
    chat_box = tester.chat_input[0]
    chat_box.set_value("Hello orchestrator")
    chat_box.run()

    # Read the latest entry from the orchestrator mailbox and validate fields
    stream = app_module.MAILBOX_ORCHESTRATOR
    latest: list[Tuple[str, Dict[str, str]]] = r.xrevrange(stream, count=1)
    assert latest, "Expected an entry to be written to orchestrator mailbox"
    _id, fields = latest[0]

    env = MessageEnvelope.from_stream_fields(fields)
    assert env.sender == HUMAN_RECIPIENT
    assert env.recipient == ORCHESTRATOR_RECIPIENT
    assert env.message == "Hello orchestrator"
    assert env.payload.get("chat_id") == _state_get(tester.session_state, "chat_id")
    assert env.payload.get("ticket_id")


@pytest.mark.integration
def test_live_receive_updates_when_human_stream_gets_entry() -> None:
    """
    Verifies the app renders assistant messages from the human mailbox.

    This test runs the app against a live Redis instance. It simulates an
    external process (like the orchestrator) adding a new message to the
    human's mailbox stream, targeted at the app's active `chat_id`. It then
    triggers a rerun of the Streamlit app and asserts that the new message is
    rendered correctly in the chat history.
    """
    try:
        r = _live_redis()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Live Redis not available: {exc}")

    import quadracode_ui.app as app_module

    tester = AppTest.from_file(app_module.__file__, default_timeout=2.0)
    tester.run()

    # Obtain the active chat id so we can target the message correctly
    chat_id = _state_get(tester.session_state, "chat_id")
    assert chat_id, "App did not initialize a chat_id"

    # Append a new entry to the human mailbox for this chat
    env = MessageEnvelope(
        sender=ORCHESTRATOR_RECIPIENT,
        recipient=HUMAN_RECIPIENT,
        message="Hello from Redis",
        payload={"chat_id": chat_id, "ticket_id": "it-works", "messages": []},
    )
    r.xadd(app_module.MAILBOX_HUMAN, env.to_stream_fields())

    # Trigger a rerun so the app polls and renders the new message
    # Allow brief propagation time on slower environments
    time.sleep(0.1)
    tester.run()

    # The last chat message should be the assistant response we injected
    assert tester.chat_message
    last = tester.chat_message[-1]
    assert last.markdown
    assert last.markdown[0].value == "Hello from Redis"


def _state_get(session_state, key: str, default=None):
    """
    Safely retrieves a value from the Streamlit session state.

    This helper handles potential `KeyError` exceptions and provides a consistent
    way to access session state values, returning a default if the key is not
    found. It is necessary for compatibility with different Streamlit versions
    and testing contexts where direct dictionary access might fail.

    Args:
        session_state: The Streamlit session state object.
        key: The key of the value to retrieve.
        default: The default value to return if the key is not found.

    Returns:
        The value from the session state, or the default.
    """
    getter = getattr(session_state, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except KeyError:
            return default
    try:
        return session_state[key]
    except KeyError:
        return default

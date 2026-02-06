"""
Message handling utilities for Quadracode UI.

Provides functions for sending, receiving, and managing messages via Redis Streams.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import redis
import streamlit as st

from quadracode_contracts import (
    HUMAN_CLONE_RECIPIENT,
    HUMAN_RECIPIENT,
    ORCHESTRATOR_RECIPIENT,
    SUPERVISOR_RECIPIENT,
    MessageEnvelope,
)
from quadracode_contracts.messaging import mailbox_key


def active_supervisor() -> str:
    """
    Returns the ID of the currently active supervisor.

    Defaults to `HUMAN_RECIPIENT` if the value in the session state is invalid.
    """
    value = st.session_state.get("supervisor_recipient")
    if value in {HUMAN_RECIPIENT, HUMAN_CLONE_RECIPIENT, SUPERVISOR_RECIPIENT}:
        return value
    return HUMAN_RECIPIENT


def set_supervisor(recipient: str, chat_id: str | None = None) -> None:
    """
    Sets the active supervisor for the application and optionally for a specific chat.

    Updates the global `supervisor_recipient` in the session state and also
    associates the supervisor with a chat ID if provided.
    """
    target = recipient if recipient in {HUMAN_RECIPIENT, HUMAN_CLONE_RECIPIENT, SUPERVISOR_RECIPIENT} else HUMAN_RECIPIENT
    st.session_state.supervisor_recipient = target
    supervisors = st.session_state.get("chat_supervisors")
    if not isinstance(supervisors, dict):
        supervisors = {}
        st.session_state.chat_supervisors = supervisors
    if chat_id:
        supervisors[chat_id] = target


def supervisor_mailbox() -> str:
    """Returns the Redis mailbox key for the currently active supervisor."""
    return mailbox_key(active_supervisor())


def send_message(
    client: redis.Redis,
    message: str,
    chat_id: str,
    mode: str = "human",
    autonomous_settings: dict[str, Any] | None = None,
) -> str:
    """
    Constructs and sends a message envelope to the orchestrator.

    Args:
        client: The Redis client.
        message: The text content of the message.
        chat_id: The chat ID for the message.
        mode: The mode ('human' or 'supervisor').
        autonomous_settings: Optional autonomous mode settings.

    Returns:
        The `ticket_id` for the sent message, used for tracking.
    """
    ticket_id = uuid.uuid4().hex
    supervisor = SUPERVISOR_RECIPIENT if mode in {"supervisor", "human_clone"} else HUMAN_RECIPIENT

    payload = {
        "chat_id": chat_id,
        "ticket_id": ticket_id,
        "supervisor": supervisor,
    }

    if autonomous_settings:
        payload["mode"] = "autonomous"
        payload["autonomous_settings"] = autonomous_settings
        payload.setdefault("task_goal", message)

    envelope = MessageEnvelope(
        sender=supervisor,
        recipient=ORCHESTRATOR_RECIPIENT,
        message=message,
        payload=payload,
    )

    mailbox = mailbox_key(ORCHESTRATOR_RECIPIENT)
    client.xadd(mailbox, envelope.to_stream_fields())
    return ticket_id


def send_emergency_stop(client: redis.Redis, chat_id: str, supervisor: str) -> str:
    """
    Sends an emergency stop signal to the orchestrator for the current chat.

    Args:
        client: The Redis client for sending the message.
        chat_id: The chat ID to stop.
        supervisor: The supervisor making the request.

    Returns:
        The ticket_id of the stop message.
    """
    ticket_id = uuid.uuid4().hex
    payload = {
        "chat_id": chat_id,
        "ticket_id": ticket_id,
        "supervisor": supervisor,
        "autonomous_control": {"action": "emergency_stop"},
    }

    envelope = MessageEnvelope(
        sender=supervisor,
        recipient=ORCHESTRATOR_RECIPIENT,
        message="Emergency stop requested by human.",
        payload=payload,
    )

    mailbox = mailbox_key(ORCHESTRATOR_RECIPIENT)
    client.xadd(mailbox, envelope.to_stream_fields())
    return ticket_id


def poll_messages(
    client: redis.Redis,
    mailbox: str,
    last_id: str,
    chat_id: str | None = None,
    count: int = 50,
) -> tuple[list[MessageEnvelope], str]:
    """
    Polls a mailbox for new messages.

    Args:
        client: The Redis client.
        mailbox: The mailbox key to poll.
        last_id: The last seen message ID.
        chat_id: Optional chat_id filter.
        count: Maximum number of messages to retrieve.

    Returns:
        A tuple of (message_list, new_last_id).
    """
    try:
        responses = client.xread({mailbox: last_id}, count=count)
    except redis.RedisError:
        return [], last_id

    matched: list[MessageEnvelope] = []
    new_last_id = last_id

    for stream_key, entries in responses:
        if stream_key != mailbox:
            continue
        for entry_id, fields in entries:
            envelope = MessageEnvelope.from_stream_fields(fields)
            if chat_id is not None:
                payload = envelope.payload or {}
                if payload.get("chat_id") != chat_id:
                    continue
            matched.append(envelope)
            if entry_id > new_last_id:
                new_last_id = entry_id

    return matched, new_last_id


def get_all_messages(
    client: redis.Redis,
    mailboxes: list[str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Fetches messages from multiple streams.

    Args:
        client: The Redis client.
        mailboxes: List of mailbox keys to fetch from.
        limit: Maximum number of messages per mailbox.

    Returns:
        A list of message dictionaries sorted by timestamp.
    """
    messages = []
    for mailbox in mailboxes:
        try:
            entries = client.xrevrange(mailbox, "+", "-", count=limit)
        except redis.RedisError:
            continue

        for msg_id, fields in entries:
            payload_str = fields.get("payload", "{}")
            try:
                payload = json.loads(payload_str) if payload_str else {}
            except json.JSONDecodeError:
                payload = {}

            messages.append({
                "id": msg_id,
                "mailbox": mailbox,
                "timestamp": fields.get("timestamp", ""),
                "sender": fields.get("sender", ""),
                "recipient": fields.get("recipient", ""),
                "message": fields.get("message", ""),
                "payload": payload,
            })

    return sorted(messages, key=lambda x: x["timestamp"], reverse=True)


def format_timestamp(timestamp_str: str) -> str:
    """
    Formats an ISO timestamp string for display.

    Args:
        timestamp_str: The ISO timestamp string.

    Returns:
        A human-readable timestamp string.
    """
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return timestamp_str



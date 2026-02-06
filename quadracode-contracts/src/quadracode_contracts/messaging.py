"""
Shared data contracts and utility functions for the Redis Streams-based
messaging system used in Quadracode.

Provides the :class:`MessageEnvelope` model — the canonical structure for all
messages passed between system components.  Every message is well-formed and
includes essential metadata (sender, recipient, timestamp).  Helper functions
construct and parse mailbox keys used to route messages to the correct Redis
stream.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Self

from pydantic import BaseModel, Field


MAILBOX_PREFIX: str = "qc:mailbox/"
ORCHESTRATOR_RECIPIENT: str = "orchestrator"
HUMAN_RECIPIENT: str = "human"
HUMAN_CLONE_RECIPIENT: str = "human_clone"
# Preferred alias — use SUPERVISOR_RECIPIENT in new code.
SUPERVISOR_RECIPIENT: str = HUMAN_CLONE_RECIPIENT


def _default_timestamp() -> str:
    """Return the current UTC time as a seconds-precision ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MessageEnvelope(BaseModel):
    """Envelope for all messages passed through the Redis Streams fabric.

    This Pydantic model ensures that every message is uniformly structured,
    containing essential metadata for routing and diagnostics.  It includes
    serialization and deserialization helpers to convert the envelope to and
    from the flat ``dict[str, str]`` format required by Redis ``XADD``.

    Attributes:
        timestamp: ISO-8601 formatted creation timestamp.
        sender: ID of the component sending the message.
        recipient: ID of the intended recipient component.
        message: String identifier for the message type.
        payload: JSON-serializable dictionary containing the message data.
    """

    timestamp: str = Field(default_factory=_default_timestamp)
    sender: str
    recipient: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_stream_fields(self) -> dict[str, str]:
        """Serialize the envelope to a Redis-compatible flat dictionary.

        Converts the :class:`MessageEnvelope` into a flat ``dict[str, str]``
        suitable for the ``XADD`` command.  The *payload* field is
        JSON-encoded with compact separators.

        Returns:
            A dictionary of string key-value pairs.
        """
        return {
            "timestamp": self.timestamp,
            "sender": self.sender,
            "recipient": self.recipient,
            "message": self.message,
            "payload": json.dumps(self.payload, separators=(",", ":")),
        }

    @classmethod
    def from_stream_fields(cls, fields: Mapping[str, str]) -> Self:
        """Deserialize a Redis stream entry back into a :class:`MessageEnvelope`.

        Designed to be robust against malformed data.  Safely parses the
        fields from a Redis stream message, including handling potential
        JSON decoding errors in the payload.

        Args:
            fields: A mapping of fields from a Redis stream entry.

        Returns:
            A :class:`MessageEnvelope` instance.
        """
        payload_raw = fields.get("payload", "{}")
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {"_raw": payload_raw}

        return cls(
            timestamp=fields.get("timestamp", _default_timestamp()),
            sender=fields.get("sender", "unknown"),
            recipient=fields.get("recipient", "unknown"),
            message=fields.get("message", ""),
            payload=payload,
        )


def mailbox_key(recipient: str) -> str:
    """Construct the Redis stream key for a given recipient.

    Args:
        recipient: The ID of the recipient.

    Returns:
        The fully-qualified Redis stream key.
    """
    return f"{MAILBOX_PREFIX}{recipient}"


def mailbox_recipient(mailbox: str) -> str:
    """Extract the recipient ID from a Redis stream key.

    Args:
        mailbox: The Redis stream key.

    Returns:
        The recipient's ID.
    """
    if mailbox.startswith(MAILBOX_PREFIX):
        return mailbox[len(MAILBOX_PREFIX) :]
    return mailbox


def agent_mailbox(agent_id: str) -> str:
    """Construct the mailbox key for a specific agent.

    Convenience wrapper around :func:`mailbox_key`.

    Args:
        agent_id: The ID of the agent.

    Returns:
        The agent's mailbox key.
    """
    return mailbox_key(agent_id)


__all__ = [
    "MessageEnvelope",
    "MAILBOX_PREFIX",
    "ORCHESTRATOR_RECIPIENT",
    "HUMAN_RECIPIENT",
    "HUMAN_CLONE_RECIPIENT",
    "SUPERVISOR_RECIPIENT",
    "mailbox_key",
    "mailbox_recipient",
    "agent_mailbox",
]

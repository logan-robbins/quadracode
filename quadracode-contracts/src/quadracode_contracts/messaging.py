"""
This module defines the shared data contracts and utility functions for the 
Redis streams-based messaging system used in Quadracode.

It provides the `MessageEnvelope` model, which is the canonical structure for all 
messages passed between system components. This ensures that all messages are 
well-formed and include essential metadata such as sender, recipient, and 
timestamp. The module also provides constants and helper functions for 
constructing and parsing mailbox keys, which are used to route messages to the 
correct Redis stream.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from pydantic import BaseModel, Field

MAILBOX_PREFIX = "qc:mailbox/"
ORCHESTRATOR_RECIPIENT = "orchestrator"
HUMAN_RECIPIENT = "human"
HUMAN_CLONE_RECIPIENT = "human_clone"


def _default_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MessageEnvelope(BaseModel):
    """
    Defines the envelope for all messages passed through the Redis streams.

    This Pydantic model ensures that every message is uniformly structured, 
    containing essential metadata for routing and diagnostics. It includes 
    serialization and deserialization methods to convert the envelope to and 
    from the format required by Redis streams.

    Attributes:
        timestamp: The ISO-8601 formatted timestamp of when the message was created.
        sender: The ID of the component sending the message.
        recipient: The ID of the component intended to receive the message.
        message: A string identifier for the type of message being sent.
        payload: A JSON-serializable dictionary containing the message's data.
    """

    timestamp: str = Field(default_factory=_default_timestamp)
    sender: str
    recipient: str
    message: str
    payload: Dict[str, Any] = Field(default_factory=dict)

    def to_stream_fields(self) -> Dict[str, str]:
        """
        Serializes the envelope to a dictionary suitable for Redis streams.

        This method converts the `MessageEnvelope` into a flat dictionary of 
        strings, which is the format required by the `XADD` command in Redis. The 
        payload is JSON-encoded.

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
    def from_stream_fields(cls, fields: Mapping[str, str]) -> "MessageEnvelope":
        """
        Deserializes a Redis stream entry back into a `MessageEnvelope`.

        This class method is designed to be robust against malformed data. It 
        safely parses the fields from a Redis stream message, including handling 
        potential JSON decoding errors in the payload.

        Args:
            fields: A mapping of fields from a Redis stream entry.

        Returns:
            A `MessageEnvelope` instance.
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


__all__ = ["MessageEnvelope"]


def mailbox_key(recipient: str) -> str:
    """
    Constructs the Redis stream key for a given recipient.

    Args:
        recipient: The ID of the recipient.

    Returns:
        The fully-qualified Redis stream key.
    """
    return f"{MAILBOX_PREFIX}{recipient}"


def mailbox_recipient(mailbox: str) -> str:
    """
    Extracts the recipient ID from a Redis stream key.

    Args:
        mailbox: The Redis stream key.

    Returns:
        The recipient's ID.
    """
    if mailbox.startswith(MAILBOX_PREFIX):
        return mailbox[len(MAILBOX_PREFIX) :]
    return mailbox


__all__.extend(
    [
        "MAILBOX_PREFIX",
        "ORCHESTRATOR_RECIPIENT",
        "HUMAN_RECIPIENT",
        "HUMAN_CLONE_RECIPIENT",
        "mailbox_key",
        "mailbox_recipient",
        "agent_mailbox",
    ]
)


def agent_mailbox(agent_id: str) -> str:
    """
    Constructs the mailbox key for a specific agent.

    This is a convenience function that wraps `mailbox_key` for agents.

    Args:
        agent_id: The ID of the agent.

    Returns:
        The agent's mailbox key.
    """
    return mailbox_key(agent_id)

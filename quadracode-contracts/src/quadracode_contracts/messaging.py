"""Shared messaging contracts for Quadracode Redis streams."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from pydantic import BaseModel, Field

MAILBOX_PREFIX = "qc:mailbox/"
ORCHESTRATOR_RECIPIENT = "orchestrator"
HUMAN_RECIPIENT = "human"


def _default_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MessageEnvelope(BaseModel):
    """Envelope describing a message passed through Redis streams."""

    timestamp: str = Field(default_factory=_default_timestamp)
    sender: str
    recipient: str
    message: str
    payload: Dict[str, Any] = Field(default_factory=dict)

    def to_stream_fields(self) -> Dict[str, str]:
        """Serialize the envelope to Redis stream field/value pairs."""

        return {
            "timestamp": self.timestamp,
            "sender": self.sender,
            "recipient": self.recipient,
            "message": self.message,
            "payload": json.dumps(self.payload, separators=(",", ":")),
        }

    @classmethod
    def from_stream_fields(cls, fields: Mapping[str, str]) -> "MessageEnvelope":
        """Deserialize a Redis stream entry back into an envelope."""

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
    return f"{MAILBOX_PREFIX}{recipient}"


def mailbox_recipient(mailbox: str) -> str:
    if mailbox.startswith(MAILBOX_PREFIX):
        return mailbox[len(MAILBOX_PREFIX) :]
    return mailbox


__all__.extend(
    [
        "MAILBOX_PREFIX",
        "ORCHESTRATOR_RECIPIENT",
        "HUMAN_RECIPIENT",
        "mailbox_key",
        "mailbox_recipient",
        "agent_mailbox",
    ]
)


def agent_mailbox(agent_id: str) -> str:
    return mailbox_key(agent_id)

"""Contracts and schemas shared between Quadracode agents and orchestrator."""

from .messaging import (
    MessageEnvelope,
    MAILBOX_PREFIX,
    ORCHESTRATOR_RECIPIENT,
    HUMAN_RECIPIENT,
    mailbox_key,
    mailbox_recipient,
    agent_mailbox,
)

__all__ = [
    "MessageEnvelope",
    "MAILBOX_PREFIX",
    "ORCHESTRATOR_RECIPIENT",
    "HUMAN_RECIPIENT",
    "mailbox_key",
    "mailbox_recipient",
    "agent_mailbox",
]

__version__ = "0.1.0"

"""Contracts and schemas shared between Quadracode agents and orchestrator."""

from .autonomous import (
    AutonomousCheckpointRecord,
    AutonomousCritiqueRecord,
    AutonomousEscalationRecord,
    AutonomousRoutingDirective,
)
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
    "AutonomousRoutingDirective",
    "AutonomousCheckpointRecord",
    "AutonomousCritiqueRecord",
    "AutonomousEscalationRecord",
]

__version__ = "0.1.0"

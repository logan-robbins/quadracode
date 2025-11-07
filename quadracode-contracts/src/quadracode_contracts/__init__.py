"""Contracts and schemas shared between Quadracode agents and orchestrator."""

from .autonomous import (
    AutonomousCheckpointRecord,
    AutonomousEscalationRecord,
    AutonomousRoutingDirective,
    CritiqueCategory,
    CritiqueSeverity,
    HypothesisCritiqueRecord,
)
from .human_clone import (
    HumanCloneExhaustionMode,
    HumanCloneTrigger,
)
from .messaging import (
    MessageEnvelope,
    MAILBOX_PREFIX,
    ORCHESTRATOR_RECIPIENT,
    HUMAN_RECIPIENT,
    HUMAN_CLONE_RECIPIENT,
    mailbox_key,
    mailbox_recipient,
    agent_mailbox,
)
from .workspace import (
    DEFAULT_WORKSPACE_MOUNT,
    WorkspaceDescriptor,
    WorkspaceCommandResult,
    WorkspaceCopyResult,
    WorkspaceSnapshotRecord,
    collect_environment_keys,
    normalize_workspace_name,
)

__all__ = [
    "MessageEnvelope",
    "MAILBOX_PREFIX",
    "ORCHESTRATOR_RECIPIENT",
    "HUMAN_RECIPIENT",
    "HUMAN_CLONE_RECIPIENT",
    "mailbox_key",
    "mailbox_recipient",
    "agent_mailbox",
    "AutonomousRoutingDirective",
    "AutonomousCheckpointRecord",
    "AutonomousEscalationRecord",
    "CritiqueCategory",
    "CritiqueSeverity",
    "HypothesisCritiqueRecord",
    "HumanCloneTrigger",
    "HumanCloneExhaustionMode",
    "DEFAULT_WORKSPACE_MOUNT",
    "WorkspaceDescriptor",
    "WorkspaceCommandResult",
    "WorkspaceCopyResult",
    "WorkspaceSnapshotRecord",
    "collect_environment_keys",
    "normalize_workspace_name",
]

__version__ = "0.1.0"

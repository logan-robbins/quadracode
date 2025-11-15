"""
This package defines the shared data contracts and schemas used throughout the 
Quadracode ecosystem.

It serves as the single source of truth for the data structures that are 
exchanged between the orchestrator, agents, and other services. By centralizing 
these contracts, it ensures consistency and type safety across the entire 
system. The models defined here are primarily Pydantic-based, which allows for 
robust data validation and serialization. This package is a critical dependency 
for any component that participates in the Quadracode messaging and workflow 
system.
"""
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

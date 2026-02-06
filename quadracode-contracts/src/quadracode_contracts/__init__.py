"""
Shared data contracts and schemas used throughout the Quadracode ecosystem.

This package is the single source of truth for the data structures exchanged
between the orchestrator, agents, and supporting services.  Centralizing these
contracts ensures consistency and type safety across the entire system.  The
models are Pydantic v2-based, providing robust data validation and
serialization.
"""
from .agent_id import generate_agent_id
from .agent_registry import (
    AgentHeartbeat,
    AgentInfo,
    AgentListResponse,
    AgentRegistrationRequest,
    AgentStatus,
    RegistryStats,
)
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
    HUMAN_CLONE_RECIPIENT,
    HUMAN_RECIPIENT,
    MAILBOX_PREFIX,
    ORCHESTRATOR_RECIPIENT,
    SUPERVISOR_RECIPIENT,
    MessageEnvelope,
    agent_mailbox,
    mailbox_key,
    mailbox_recipient,
)
from .workspace import (
    DEFAULT_WORKSPACE_MOUNT,
    WorkspaceCommandResult,
    WorkspaceCopyResult,
    WorkspaceDescriptor,
    WorkspaceSnapshotRecord,
    collect_environment_keys,
    normalize_workspace_name,
)

__all__ = [
    # agent_id
    "generate_agent_id",
    # agent_registry
    "AgentStatus",
    "AgentRegistrationRequest",
    "AgentHeartbeat",
    "AgentInfo",
    "AgentListResponse",
    "RegistryStats",
    # autonomous
    "AutonomousRoutingDirective",
    "AutonomousCheckpointRecord",
    "AutonomousEscalationRecord",
    "CritiqueCategory",
    "CritiqueSeverity",
    "HypothesisCritiqueRecord",
    # human_clone
    "HumanCloneTrigger",
    "HumanCloneExhaustionMode",
    # messaging
    "MessageEnvelope",
    "MAILBOX_PREFIX",
    "ORCHESTRATOR_RECIPIENT",
    "HUMAN_RECIPIENT",
    "HUMAN_CLONE_RECIPIENT",
    "SUPERVISOR_RECIPIENT",
    "mailbox_key",
    "mailbox_recipient",
    "agent_mailbox",
    # workspace
    "DEFAULT_WORKSPACE_MOUNT",
    "WorkspaceDescriptor",
    "WorkspaceCommandResult",
    "WorkspaceCopyResult",
    "WorkspaceSnapshotRecord",
    "collect_environment_keys",
    "normalize_workspace_name",
]

__version__ = "0.2.0"

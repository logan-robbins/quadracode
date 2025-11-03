from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, TypedDict, cast, Literal

from langchain_core.messages import AnyMessage, message_to_dict, messages_from_dict
from langgraph.graph import add_messages


class AutonomousMilestone(TypedDict, total=False):
    """Autonomous milestone tracking record."""

    milestone: int
    title: Optional[str]
    status: Literal["pending", "in_progress", "complete", "blocked"]
    summary: Optional[str]
    next_steps: List[str]
    updated_at: Optional[str]


class AutonomousErrorRecord(TypedDict, total=False):
    """Autonomous error history record."""

    error_type: str
    description: str
    recovery_attempts: List[str]
    escalated: bool
    resolved: bool
    timestamp: Optional[str]


class _RuntimeStateRequired(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


class RuntimeState(_RuntimeStateRequired, total=False):
    autonomous_mode: bool
    task_goal: Optional[str]
    current_phase: Optional[str]
    iteration_count: int
    milestones: List[AutonomousMilestone]
    error_history: List[AutonomousErrorRecord]
    autonomous_started_at: Optional[str]
    last_iteration_at: Optional[str]
    iteration_limit_triggered: bool
    runtime_limit_triggered: bool
    autonomous_routing: Dict[str, Any]
    autonomous_settings: Dict[str, Any]
    thread_id: Optional[str]


class ContextSegment(TypedDict):
    """Individual context segment metadata for ACE/MemAct."""

    id: str
    content: str
    type: str
    priority: int
    token_count: int
    timestamp: str
    decay_rate: float
    compression_eligible: bool
    restorable_reference: Optional[str]


class MemoryCheckpoint(TypedDict):
    """Persisted snapshot of context state."""

    checkpoint_id: str
    timestamp: str
    milestone: Optional[int]
    summary: str
    full_context_path: str
    token_count: int
    quality_score: float


class ContextEngineState(RuntimeState):
    """Extended runtime state used by the context engineering node."""

    # Context Management
    context_window_used: int
    context_window_max: int
    context_quality_score: float

    # Working Memory (In-Context)
    working_memory: Dict[str, Any]
    context_segments: List[ContextSegment]

    # External Memory (File System)
    external_memory_index: Dict[str, str]
    memory_checkpoints: List[MemoryCheckpoint]

    # Progressive Loading
    pending_context: List[str]
    context_hierarchy: Dict[str, int]
    prefetch_queue: List[Dict[str, Any]]

    # Evolution Tracking (ACE)
    context_playbook: Dict[str, Any]
    reflection_log: List[Dict[str, Any]]
    curation_rules: List[Dict[str, Any]]

    # Metrics
    compression_ratio: float
    retrieval_accuracy: float
    attention_distribution: Dict[str, float]
    context_quality_components: Dict[str, float]
    metrics_log: List[Dict[str, Any]]
    governor_plan: Dict[str, Any]
    governor_prompt_outline: Dict[str, Any]
    skills_catalog: List[Dict[str, Any]]
    active_skills_metadata: List[Dict[str, Any]]
    loaded_skills: Dict[str, Dict[str, Any]]
    last_curation_summary: Dict[str, Any]
    recent_loads: List[Dict[str, Any]]
    recent_externalizations: List[Dict[str, Any]]


def make_initial_context_engine_state(
    *,
    context_window_max: int = 0,
) -> ContextEngineState:
    """Create a baseline ContextEngineState with safe defaults."""

    return cast(
        ContextEngineState,
        {
            "messages": [],
            "context_window_used": 0,
            "context_window_max": context_window_max,
            "context_quality_score": 0.0,
            "working_memory": {},
            "context_segments": [],
            "external_memory_index": {},
            "memory_checkpoints": [],
            "pending_context": [],
            "context_hierarchy": {},
            "prefetch_queue": [],
            "context_playbook": {},
            "reflection_log": [],
            "curation_rules": [],
            "compression_ratio": 0.0,
            "retrieval_accuracy": 0.0,
            "attention_distribution": {},
            "context_quality_components": {},
            "metrics_log": [],
            "governor_plan": {},
            "governor_prompt_outline": {},
            "skills_catalog": [],
            "active_skills_metadata": [],
            "loaded_skills": {},
            "last_curation_summary": {},
            "recent_loads": [],
            "recent_externalizations": [],
        },
    )


def serialize_context_engine_state(state: ContextEngineState) -> Dict[str, Any]:
    """Produce a JSON-friendly representation of the context state."""

    return {
        **state,
        "messages": [message_to_dict(message) for message in state["messages"]],
    }


def deserialize_context_engine_state(payload: Dict[str, Any]) -> ContextEngineState:
    """Rehydrate a ContextEngineState from a serialized payload."""

    messages_payload = payload.get("messages", [])
    state = {**payload}
    state["messages"] = list(messages_from_dict(messages_payload))
    return cast(ContextEngineState, state)

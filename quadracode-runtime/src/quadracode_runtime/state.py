from __future__ import annotations

from copy import deepcopy
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Annotated,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    TypedDict,
    cast,
    Literal,
)

from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage, message_to_dict, messages_from_dict
from langgraph.graph import add_messages

from quadracode_contracts import WorkspaceSnapshotRecord

from .observability import get_meta_observer
from .time_travel import get_time_travel_recorder
from .invariants import check_transition_invariants, mark_rejection_requires_tests


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
    workspace: Dict[str, Any]


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


class ExhaustionMode(str, Enum):
    """Enumerated exhaustion modes for PRP cycles and context operations."""

    NONE = "none"
    CONTEXT_SATURATION = "context_saturation"
    RETRY_DEPLETION = "retry_depletion"
    TOOL_BACKPRESSURE = "tool_backpressure"
    LLM_STOP = "llm_stop"
    TEST_FAILURE = "test_failure"
    HYPOTHESIS_EXHAUSTED = "hypothesis_exhausted"
    PREDICTED_EXHAUSTION = "predicted_exhaustion"


# Alias retained for backwards compatibility with earlier research notes
ExhaustionEnum = ExhaustionMode


class RefinementLedgerEntry(BaseModel):
    """Structured record capturing PRP refinement cycle metadata."""

    cycle_id: str
    timestamp: datetime
    hypothesis: str
    status: str
    outcome_summary: str
    exhaustion_trigger: ExhaustionMode | None = None
    test_results: Dict[str, Any] | None = None
    strategy: str | None = None
    novelty_score: float | None = None
    novelty_basis: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    predicted_success_probability: float | None = None
    causal_links: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def formatted_summary(self) -> str:
        """Return a compact string summary for prompt injection."""

        trigger = (
            f" (triggered by {self.exhaustion_trigger.value})"
            if self.exhaustion_trigger
            else ""
        )
        status_line = f"Status: {self.status}{trigger}"
        outcome = self.outcome_summary.strip()
        if len(outcome) > 280:
            outcome = outcome[:277] + "..."
        extra_tags: List[str] = []
        if self.strategy:
            extra_tags.append(f"strategy={self.strategy}")
        if self.novelty_score is not None:
            extra_tags.append(f"novelty={self.novelty_score:.2f}")
        if self.predicted_success_probability is not None:
            extra_tags.append(
                f"p_success={self.predicted_success_probability:.2f}"
            )
        lines = [
            f"Cycle {self.cycle_id} @ {self.timestamp.isoformat()}",
            status_line,
            f"Hypothesis: {self.hypothesis}",
            f"Outcome: {outcome}",
        ]
        if extra_tags:
            lines.append("Signals: " + ", ".join(extra_tags))
        if self.test_results:
            lines.append(f"Tests: {self._format_test_results()}")
        return " | ".join(lines)

    def _format_test_results(self) -> str:
        if not self.test_results:
            return "n/a"
        if isinstance(self.test_results, str):
            return self.test_results
        if isinstance(self.test_results, dict):
            summary_parts = []
            for key, value in list(self.test_results.items())[:5]:
                summary_parts.append(f"{key}={value}")
            remainder = len(self.test_results) - len(summary_parts)
            if remainder > 0:
                summary_parts.append(f"+{remainder} more")
            return ", ".join(summary_parts)
        if isinstance(self.test_results, list):
            preview = ", ".join(str(item) for item in self.test_results[:5])
            if len(self.test_results) > 5:
                preview += ", ..."
            return preview
        return str(self.test_results)


class PRPState(str, Enum):
    """Operational states for the Perpetual Refinement Protocol."""

    HYPOTHESIZE = "hypothesize"
    EXECUTE = "execute"
    TEST = "test"
    CONCLUDE = "conclude"
    PROPOSE = "propose"


class PRPTransition(BaseModel):
    """Transition rule within the PRP finite state automaton."""

    source: PRPState
    target: PRPState
    description: str = ""
    allow_if_exhaustion_in: set[ExhaustionMode] | None = Field(default=None)
    block_if_exhaustion_in: set[ExhaustionMode] | None = Field(default=None)
    requires_human_clone: bool = False


class PRPInvalidTransitionError(RuntimeError):
    """Raised when a PRP transition violates configured guards."""

    def __init__(
        self,
        *,
        source: PRPState,
        target: PRPState,
        exhaustion_mode: ExhaustionMode,
        human_clone_triggered: bool,
        reason: str,
        description: str = "",
    ) -> None:
        message = (
            f"Invalid PRP transition {source.value}→{target.value}: {reason}"
        )
        super().__init__(message)
        self.telemetry = {
            "from_state": source.value,
            "to_state": target.value,
            "exhaustion_mode": exhaustion_mode.value,
            "human_clone_triggered": human_clone_triggered,
            "reason": reason,
            "description": description,
        }


class PRPStateMachine:
    """Finite-state automaton describing the PRP control loop."""

    def __init__(
        self,
        transitions: Iterable[PRPTransition],
        *,
        initial_state: PRPState = PRPState.HYPOTHESIZE,
    ) -> None:
        self.initial_state = initial_state
        self._graph: Dict[PRPState, Dict[PRPState, PRPTransition]] = {}
        for transition in transitions:
            self._graph.setdefault(transition.source, {})[transition.target] = transition

    def get_transition(self, source: PRPState, target: PRPState) -> PRPTransition | None:
        return self._graph.get(source, {}).get(target)

    def validate_transition(
        self,
        source: PRPState,
        target: PRPState,
        *,
        exhaustion_mode: ExhaustionMode,
        human_clone_triggered: bool,
    ) -> PRPTransition:
        transition = self.get_transition(source, target)
        if transition is None:
            raise PRPInvalidTransitionError(
                source=source,
                target=target,
                exhaustion_mode=exhaustion_mode,
                human_clone_triggered=human_clone_triggered,
                reason="transition_not_defined",
            )

        if transition.allow_if_exhaustion_in is not None and (
            exhaustion_mode not in transition.allow_if_exhaustion_in
        ):
            raise PRPInvalidTransitionError(
                source=source,
                target=target,
                exhaustion_mode=exhaustion_mode,
                human_clone_triggered=human_clone_triggered,
                reason="exhaustion_not_allowed",
                description=transition.description,
            )

        if transition.block_if_exhaustion_in and (
            exhaustion_mode in transition.block_if_exhaustion_in
        ):
            raise PRPInvalidTransitionError(
                source=source,
                target=target,
                exhaustion_mode=exhaustion_mode,
                human_clone_triggered=human_clone_triggered,
                reason="exhaustion_blocked",
                description=transition.description,
            )

        if transition.requires_human_clone and not human_clone_triggered:
            raise PRPInvalidTransitionError(
                source=source,
                target=target,
                exhaustion_mode=exhaustion_mode,
                human_clone_triggered=human_clone_triggered,
                reason="human_clone_required",
                description=transition.description,
            )

        return transition


DEFAULT_PRP_TRANSITIONS: List[PRPTransition] = [
    PRPTransition(
        source=PRPState.HYPOTHESIZE,
        target=PRPState.EXECUTE,
        description="Move from hypothesis generation into execution planning.",
        block_if_exhaustion_in={
            ExhaustionMode.RETRY_DEPLETION,
            ExhaustionMode.TOOL_BACKPRESSURE,
        },
    ),
    PRPTransition(
        source=PRPState.EXECUTE,
        target=PRPState.TEST,
        description="After execution, evaluate results through testing.",
        block_if_exhaustion_in={ExhaustionMode.TOOL_BACKPRESSURE},
    ),
    PRPTransition(
        source=PRPState.EXECUTE,
        target=PRPState.HYPOTHESIZE,
        description="Execution exhaustion triggers hypothesis refinement.",
        allow_if_exhaustion_in={
            ExhaustionMode.RETRY_DEPLETION,
            ExhaustionMode.TOOL_BACKPRESSURE,
            ExhaustionMode.PREDICTED_EXHAUSTION,
        },
    ),
    PRPTransition(
        source=PRPState.TEST,
        target=PRPState.CONCLUDE,
        description="Tests pass, enabling conclusion synthesis.",
        block_if_exhaustion_in={
            ExhaustionMode.TEST_FAILURE,
            ExhaustionMode.HYPOTHESIS_EXHAUSTED,
        },
    ),
    PRPTransition(
        source=PRPState.TEST,
        target=PRPState.HYPOTHESIZE,
        description="Failed tests cycle the protocol back to hypothesis refinement.",
        allow_if_exhaustion_in={
            ExhaustionMode.TEST_FAILURE,
            ExhaustionMode.HYPOTHESIS_EXHAUSTED,
        },
    ),
    PRPTransition(
        source=PRPState.CONCLUDE,
        target=PRPState.PROPOSE,
        description="Package conclusions into a proposal for HumanClone review.",
    ),
    PRPTransition(
        source=PRPState.CONCLUDE,
        target=PRPState.EXECUTE,
        description="Context-related exhaustion re-enters execution for adjustments.",
        allow_if_exhaustion_in={
            ExhaustionMode.CONTEXT_SATURATION,
            ExhaustionMode.TOOL_BACKPRESSURE,
        },
    ),
    PRPTransition(
        source=PRPState.PROPOSE,
        target=PRPState.HYPOTHESIZE,
        description="Reviewer rejection demands a new hypothesis cycle.",
        requires_human_clone=True,
    ),
]


PRP_STATE_MACHINE = PRPStateMachine(DEFAULT_PRP_TRANSITIONS)


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


class QuadraCodeState(ContextEngineState, total=False):
    """Primary orchestrator state including meta-cognitive signals."""

    is_in_prp: bool
    prp_cycle_count: int
    prp_state: PRPState
    refinement_ledger: List[RefinementLedgerEntry]
    exhaustion_mode: ExhaustionMode
    exhaustion_probability: float
    exhaustion_recovery_log: List[Dict[str, Any]]
    refinement_memory_block: str
    prp_telemetry: List[Dict[str, Any]]
    human_clone_requirements: List[str]
    human_clone_trigger: Dict[str, Any]
    last_test_suite_result: Dict[str, Any]
    last_property_test_result: Dict[str, Any]
    property_test_results: List[Dict[str, Any]]
    debugger_agents: List[Dict[str, Any]]
    workspace_snapshots: List[WorkspaceSnapshotRecord]
    workspace_validation: Dict[str, Any]
    critique_backlog: List[Dict[str, Any]]
    hypothesis_cycle_metrics: Dict[str, Any]
    time_travel_log: List[Dict[str, Any]]
    invariants: Dict[str, Any]
    autonomy_counters: Dict[str, Any]
    deliberative_plan: Dict[str, Any]
    deliberative_intermediate_states: List[Dict[str, Any]]
    deliberative_synopsis: str
    counterfactual_register: List[Dict[str, Any]]
    causal_graph_snapshot: Dict[str, Any]
    planning_uncertainty: float
    planning_success_probability: float
    episodic_memory: List[Dict[str, Any]]
    semantic_memory: List[Dict[str, Any]]
    memory_consolidation_log: List[Dict[str, Any]]
    memory_guidance: Dict[str, Any]


def make_initial_context_engine_state(
    *,
    context_window_max: int = 0,
) -> QuadraCodeState:
    """Create a baseline QuadraCodeState with safe defaults."""

    return cast(
        QuadraCodeState,
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
            "is_in_prp": False,
            "prp_cycle_count": 0,
            "prp_state": PRP_STATE_MACHINE.initial_state,
            "refinement_ledger": [],
            "exhaustion_mode": ExhaustionMode.NONE,
            "exhaustion_probability": 0.0,
            "exhaustion_recovery_log": [],
            "refinement_memory_block": "",
            "prp_telemetry": [],
            "human_clone_requirements": [],
            "human_clone_trigger": {},
            "last_test_suite_result": {},
            "last_property_test_result": {},
            "property_test_results": [],
            "debugger_agents": [],
            "critique_backlog": [],
            "hypothesis_cycle_metrics": {},
            "workspace_snapshots": [],
            "workspace_validation": {
                "status": "unknown",
                "last_checksum": None,
                "validated_at": None,
                "failure_count": 0,
                "last_error": None,
            },
            "time_travel_log": [],
            "deliberative_plan": {},
            "deliberative_intermediate_states": [],
            "deliberative_synopsis": "",
            "counterfactual_register": [],
            "causal_graph_snapshot": {},
            "planning_uncertainty": 0.0,
            "planning_success_probability": 0.0,
            "episodic_memory": [],
            "semantic_memory": [],
            "memory_consolidation_log": [],
            "memory_guidance": {},
            "invariants": {
                "needs_test_after_rejection": False,
                "context_updated_in_cycle": False,
                "violation_log": [],
                "novelty_threshold": 0.15,
                "skepticism_gate_satisfied": False,
            },
            "autonomy_counters": {
                "false_stop_events": 0,
                "false_stop_mitigated": 0,
                "false_stop_pending": 0,
                "skepticism_challenges": 0,
            },
        },
    )


def serialize_context_engine_state(state: QuadraCodeState) -> Dict[str, Any]:
    """Produce a JSON-friendly representation of the context state."""

    ledger_payload: List[Dict[str, Any]] = []
    for entry in state.get("refinement_ledger", []):
        if isinstance(entry, RefinementLedgerEntry):
            ledger_payload.append(entry.model_dump(mode="json"))
        elif isinstance(entry, dict):
            ledger_payload.append(dict(entry))
        else:
            try:
                ledger_payload.append(
                    RefinementLedgerEntry(**entry).model_dump(mode="json")  # type: ignore[arg-type]
                )
            except Exception:
                continue

    snapshots_payload: List[Dict[str, Any]] = []
    for snapshot in state.get("workspace_snapshots", []):
        if isinstance(snapshot, WorkspaceSnapshotRecord):
            snapshots_payload.append(snapshot.model_dump(mode="json"))
        elif isinstance(snapshot, dict):
            snapshots_payload.append(dict(snapshot))

    exhaustion_mode = state.get("exhaustion_mode")
    prp_state = state.get("prp_state")
    return {
        **state,
        "messages": [message_to_dict(message) for message in state["messages"]],
        "refinement_ledger": ledger_payload,
        "workspace_snapshots": snapshots_payload,
        "exhaustion_mode": exhaustion_mode.value if isinstance(exhaustion_mode, ExhaustionMode) else exhaustion_mode,
        "prp_state": prp_state.value if isinstance(prp_state, PRPState) else prp_state,
        "time_travel_log": list(state.get("time_travel_log", []))[-200:],
    }


def deserialize_context_engine_state(payload: Dict[str, Any]) -> QuadraCodeState:
    """Rehydrate a QuadraCodeState from a serialized payload."""

    messages_payload = payload.get("messages", [])
    state = {**payload}
    state["messages"] = list(messages_from_dict(messages_payload))
    ledger_payload = payload.get("refinement_ledger", [])
    hydrated_ledger: List[RefinementLedgerEntry] = []
    if isinstance(ledger_payload, list):
        for entry in ledger_payload:
            if isinstance(entry, RefinementLedgerEntry):
                hydrated_ledger.append(entry)
                continue
            if isinstance(entry, dict):
                try:
                    hydrated_ledger.append(RefinementLedgerEntry(**entry))
                except Exception:
                    continue
    state["refinement_ledger"] = hydrated_ledger

    snapshots_payload = payload.get("workspace_snapshots", [])
    hydrated_snapshots: List[WorkspaceSnapshotRecord] = []
    if isinstance(snapshots_payload, list):
        for record in snapshots_payload:
            if isinstance(record, WorkspaceSnapshotRecord):
                hydrated_snapshots.append(record)
                continue
            if isinstance(record, dict):
                try:
                    hydrated_snapshots.append(WorkspaceSnapshotRecord(**record))
                except Exception:
                    continue
    state["workspace_snapshots"] = hydrated_snapshots
    exhaustion_mode = state.get("exhaustion_mode")
    if isinstance(exhaustion_mode, str):
        try:
            state["exhaustion_mode"] = ExhaustionMode(exhaustion_mode)
        except ValueError:
            state["exhaustion_mode"] = ExhaustionMode.NONE
    elif not isinstance(exhaustion_mode, ExhaustionMode):
        state["exhaustion_mode"] = ExhaustionMode.NONE
    prp_state_value = state.get("prp_state")
    if isinstance(prp_state_value, str):
        try:
            state["prp_state"] = PRPState(prp_state_value)
        except ValueError:
            state["prp_state"] = PRP_STATE_MACHINE.initial_state
    elif not isinstance(prp_state_value, PRPState):
        state["prp_state"] = PRP_STATE_MACHINE.initial_state
    if "refinement_memory_block" not in state:
        state["refinement_memory_block"] = ""
    if "prp_telemetry" not in state or not isinstance(state.get("prp_telemetry"), list):
        state["prp_telemetry"] = []
    if "exhaustion_probability" not in state:
        state["exhaustion_probability"] = 0.0
    recovery_log = state.get("exhaustion_recovery_log")
    if not isinstance(recovery_log, list):
        state["exhaustion_recovery_log"] = []
    if not isinstance(state.get("workspace_snapshots"), list):
        state["workspace_snapshots"] = []
    validation_state = state.get("workspace_validation")
    if not isinstance(validation_state, dict):
        validation_state = {}
    state["workspace_validation"] = {
        "status": validation_state.get("status") or "unknown",
        "last_checksum": validation_state.get("last_checksum"),
        "validated_at": validation_state.get("validated_at"),
        "failure_count": int(validation_state.get("failure_count", 0) or 0),
        "last_error": validation_state.get("last_error"),
    }
    invariants_bucket = state.get("invariants")
    if not isinstance(invariants_bucket, dict):
        invariants_bucket = {}
    violation_log = invariants_bucket.get("violation_log")
    if not isinstance(violation_log, list):
        violation_log = []
    state["invariants"] = {
        "needs_test_after_rejection": bool(
            invariants_bucket.get("needs_test_after_rejection", False)
        ),
        "context_updated_in_cycle": bool(
            invariants_bucket.get("context_updated_in_cycle", False)
        ),
        "violation_log": violation_log,
        "novelty_threshold": float(
            invariants_bucket.get("novelty_threshold", 0.15) or 0.15
        ),
        "skepticism_gate_satisfied": bool(
            invariants_bucket.get("skepticism_gate_satisfied", False)
        ),
    }
    counters_bucket = state.get("autonomy_counters")
    if not isinstance(counters_bucket, dict):
        counters_bucket = {}
    state["autonomy_counters"] = {
        "false_stop_events": int(counters_bucket.get("false_stop_events", 0) or 0),
        "false_stop_mitigated": int(counters_bucket.get("false_stop_mitigated", 0) or 0),
        "false_stop_pending": int(counters_bucket.get("false_stop_pending", 0) or 0),
        "skepticism_challenges": int(
            counters_bucket.get("skepticism_challenges", 0) or 0
        ),
    }
    backlog = state.get("critique_backlog")
    if not isinstance(backlog, list):
        state["critique_backlog"] = []
    if not isinstance(state.get("time_travel_log"), list):
        state["time_travel_log"] = []
    if "deliberative_plan" not in state or not isinstance(state.get("deliberative_plan"), dict):
        state["deliberative_plan"] = {}
    if "deliberative_intermediate_states" not in state or not isinstance(
        state.get("deliberative_intermediate_states"), list
    ):
        state["deliberative_intermediate_states"] = []
    if not isinstance(state.get("deliberative_synopsis"), str):
        state["deliberative_synopsis"] = ""
    if "counterfactual_register" not in state or not isinstance(state.get("counterfactual_register"), list):
        state["counterfactual_register"] = []
    if "causal_graph_snapshot" not in state or not isinstance(state.get("causal_graph_snapshot"), dict):
        state["causal_graph_snapshot"] = {}
    if not isinstance(state.get("planning_uncertainty"), (int, float)):
        state["planning_uncertainty"] = 0.0
    if not isinstance(state.get("planning_success_probability"), (int, float)):
        state["planning_success_probability"] = 0.0
    if "episodic_memory" not in state or not isinstance(state.get("episodic_memory"), list):
        state["episodic_memory"] = []
    if "semantic_memory" not in state or not isinstance(state.get("semantic_memory"), list):
        state["semantic_memory"] = []
    if "memory_consolidation_log" not in state or not isinstance(state.get("memory_consolidation_log"), list):
        state["memory_consolidation_log"] = []
    if "memory_guidance" not in state or not isinstance(state.get("memory_guidance"), dict):
        state["memory_guidance"] = {}
    return cast(QuadraCodeState, state)


def _latest_ledger_entry(state: QuadraCodeState) -> RefinementLedgerEntry | None:
    ledger = state.get("refinement_ledger")
    if not isinstance(ledger, list) or not ledger:
        return None
    last_entry = ledger[-1]
    if isinstance(last_entry, RefinementLedgerEntry):
        return last_entry
    if isinstance(last_entry, dict):
        try:
            hydrated = RefinementLedgerEntry(**last_entry)
            ledger[-1] = hydrated
            return hydrated
        except Exception:
            return None
    return None


def active_cycle_id(state: QuadraCodeState) -> str:
    """Resolve the active refinement cycle identifier for observability."""

    entry = _latest_ledger_entry(state)
    if entry is not None:
        if isinstance(entry, RefinementLedgerEntry):
            return entry.cycle_id
        if isinstance(entry, dict):
            value = entry.get("cycle_id")
            if value:
                return str(value)
    return f"cycle-{int(state.get('prp_cycle_count', 0) or 0) + 1}"


def _ensure_test_result_container(entry: RefinementLedgerEntry) -> Dict[str, Any]:
    existing = entry.test_results
    if isinstance(existing, dict):
        return existing
    if existing in (None, {}):
        entry.test_results = {}
        return entry.test_results
    entry.test_results = {"legacy": existing}
    return entry.test_results


def _append_test_failure_log(
    state: QuadraCodeState,
    *,
    tool: str,
    message: str,
    extra: Dict[str, Any] | None = None,
) -> None:
    recovery_log = state.setdefault("exhaustion_recovery_log", [])
    if not isinstance(recovery_log, list):
        return
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": "test_failure",
        "details": {
            "tool": tool,
            "message": message,
        },
    }
    if extra:
        entry["details"].update(extra)
    recovery_log.append(entry)


def _ensure_autonomy_counters(state: QuadraCodeState) -> Dict[str, Any]:
    bucket = state.setdefault("autonomy_counters", {})
    if not isinstance(bucket, dict):
        bucket = {}
        state["autonomy_counters"] = bucket
    bucket.setdefault("false_stop_events", 0)
    bucket.setdefault("false_stop_mitigated", 0)
    bucket.setdefault("false_stop_pending", 0)
    bucket.setdefault("skepticism_challenges", 0)
    return bucket


def flag_false_stop_event(
    state: QuadraCodeState,
    *,
    reason: str,
    stage: str,
    evidence: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Increment false-stop counters and log telemetry for detection events."""

    counters = _ensure_autonomy_counters(state)
    counters["false_stop_events"] = int(counters.get("false_stop_events", 0)) + 1
    counters["false_stop_pending"] = int(counters.get("false_stop_pending", 0)) + 1
    payload = {
        "reason": reason,
        "stage": stage,
        "pending": counters["false_stop_pending"],
        "evidence": evidence or {},
    }
    telemetry_log = state.setdefault("prp_telemetry", [])
    if isinstance(telemetry_log, list):
        telemetry_log.append({"event": "false_stop_detected", "payload": payload})
    get_time_travel_recorder().log_transition(
        state,
        event="false_stop_detected",
        payload=payload,
        state_update={
            "autonomy_counters": {
                "false_stop_events": counters["false_stop_events"],
                "false_stop_pending": counters["false_stop_pending"],
            }
        },
    )
    return payload


def resolve_false_stop_pending(
    state: QuadraCodeState,
    *,
    stage: str,
    evidence: Dict[str, Any] | None = None,
) -> bool:
    """Resolve one pending false-stop condition if present."""

    counters = _ensure_autonomy_counters(state)
    pending = int(counters.get("false_stop_pending", 0) or 0)
    if pending <= 0:
        return False
    counters["false_stop_pending"] = pending - 1
    counters["false_stop_mitigated"] = int(counters.get("false_stop_mitigated", 0)) + 1
    payload = {
        "stage": stage,
        "remaining": counters["false_stop_pending"],
        "evidence": evidence or {},
    }
    telemetry_log = state.setdefault("prp_telemetry", [])
    if isinstance(telemetry_log, list):
        telemetry_log.append({"event": "false_stop_mitigated", "payload": payload})
    get_time_travel_recorder().log_transition(
        state,
        event="false_stop_mitigated",
        payload=payload,
        state_update={
            "autonomy_counters": {
                "false_stop_mitigated": counters["false_stop_mitigated"],
                "false_stop_pending": counters["false_stop_pending"],
            }
        },
    )
    return True


def record_skepticism_challenge(
    state: QuadraCodeState,
    *,
    source: str,
    reason: str,
    evidence: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Record a skepticism challenge event and update invariants."""

    counters = _ensure_autonomy_counters(state)
    counters["skepticism_challenges"] = int(counters.get("skepticism_challenges", 0)) + 1
    invariants = state.setdefault("invariants", {})
    if isinstance(invariants, dict):
        invariants["skepticism_gate_satisfied"] = True
    payload = {
        "source": source,
        "reason": reason,
        "evidence": evidence or {},
        "total_challenges": counters["skepticism_challenges"],
    }
    telemetry_log = state.setdefault("prp_telemetry", [])
    if isinstance(telemetry_log, list):
        telemetry_log.append({"event": "skepticism_challenge", "payload": payload})
    get_time_travel_recorder().log_transition(
        state,
        event="skepticism_challenge",
        payload=payload,
        state_update={
            "autonomy_counters": {
                "skepticism_challenges": counters["skepticism_challenges"],
            }
        },
    )
    return payload


def record_test_suite_result(state: QuadraCodeState, result: Dict[str, Any]) -> None:
    """Persist structured test telemetry and propagate exhaustion signals."""

    normalized = deepcopy(result)
    state["last_test_suite_result"] = normalized

    cycle_id = active_cycle_id(state)
    ledger_entry = _latest_ledger_entry(state)
    if ledger_entry is not None:
        container = _ensure_test_result_container(ledger_entry)
        container["full_suite"] = normalized
        if "overall_status" in normalized:
            container["overall_status"] = normalized["overall_status"]

    overall_status = str(normalized.get("overall_status") or "").lower()
    if overall_status == "failed":
        state["exhaustion_mode"] = ExhaustionMode.TEST_FAILURE
        if ledger_entry is not None:
            ledger_entry.exhaustion_trigger = ExhaustionMode.TEST_FAILURE
        _append_test_failure_log(
            state,
            tool="run_full_test_suite",
            message="Automated regression suite failed; remediation required.",
            extra={"overall_status": overall_status},
        )
    elif overall_status in {"passed", "pass", "success"}:
        resolve_false_stop_pending(
            state,
            stage="test_suite",
            evidence=normalized,
        )

    remediation = normalized.get("remediation")
    if isinstance(remediation, dict):
        debugger_agents = state.setdefault("debugger_agents", [])
        if isinstance(debugger_agents, list):
            debugger_agents.append(remediation)

    observer = get_meta_observer()
    observer.publish_test_result(
        "suite",
        {
            "cycle_id": cycle_id,
            "result": normalized,
        },
    )
    observer.record_test_value(
        state,
        cycle_id=cycle_id,
        status=overall_status,
        payload=normalized,
        test_type="suite",
    )
    # Clear invariant that requires tests after a rejection
    from .invariants import clear_test_requirement  # local import to avoid cycles
    clear_test_requirement(state)


def record_property_test_result(state: QuadraCodeState, payload: Dict[str, Any]) -> None:
    """Persist property-testing telemetry and surface failures."""

    source = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(source, dict):
        merged: Dict[str, Any] = dict(source)
        if "property_name" not in merged and isinstance(payload, dict) and "property_name" in payload:
            merged["property_name"] = payload["property_name"]
        normalized = deepcopy(merged)
    else:
        normalized = deepcopy(payload)

    state["last_property_test_result"] = normalized
    property_log = state.setdefault("property_test_results", [])
    if isinstance(property_log, list):
        property_log.append(normalized)
        state["property_test_results"] = property_log[-20:]

    status = str(normalized.get("status") or "").lower()

    cycle_id = active_cycle_id(state)
    ledger_entry = _latest_ledger_entry(state)
    if ledger_entry is not None:
        container = _ensure_test_result_container(ledger_entry)
        property_results = container.setdefault("property_tests", [])
        if isinstance(property_results, list):
            property_results.append(normalized)
        container["last_property_status"] = status

    if status == "failed":
        state["exhaustion_mode"] = ExhaustionMode.TEST_FAILURE
        if ledger_entry is not None:
            ledger_entry.exhaustion_trigger = ExhaustionMode.TEST_FAILURE
        failure_message = str(normalized.get("failure_message") or "Property test failure.")
        failing_example = normalized.get("failing_example")
        extra: Dict[str, Any] = {}
        if failing_example is not None:
            extra["failing_example"] = failing_example
        property_name = normalized.get("property_name") or payload.get("property_name")
        if property_name:
            extra["property_name"] = property_name
        _append_test_failure_log(
            state,
            tool="generate_property_tests",
            message=failure_message,
            extra=extra,
        )
    elif status in {"passed", "pass", "success"}:
        resolve_false_stop_pending(
            state,
            stage="property_tests",
            evidence=normalized,
        )

    observer = get_meta_observer()
    observer.publish_test_result(
        "property",
        {
            "cycle_id": cycle_id,
            "result": normalized,
        },
    )
    observer.record_test_value(
        state,
        cycle_id=cycle_id,
        status=status,
        payload=normalized,
        test_type="property",
    )
    # Clear invariant that requires tests after a rejection
    from .invariants import clear_test_requirement  # local import to avoid cycles
    clear_test_requirement(state)


def add_refinement_ledger_entry(
    state: QuadraCodeState,
    entry: RefinementLedgerEntry | Dict[str, Any],
) -> None:
    """Append a ledger entry to the state, normalizing payloads."""

    if isinstance(entry, dict) and not isinstance(entry, RefinementLedgerEntry):
        payload = dict(entry)
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str):
            try:
                payload["timestamp"] = datetime.fromisoformat(timestamp)
            except ValueError:
                payload["timestamp"] = datetime.now(timezone.utc)
        elif not isinstance(timestamp, datetime):
            payload["timestamp"] = datetime.now(timezone.utc)
        exhaustion_value = payload.get("exhaustion_trigger")
        if isinstance(exhaustion_value, str):
            try:
                payload["exhaustion_trigger"] = ExhaustionMode(exhaustion_value)
            except ValueError:
                payload["exhaustion_trigger"] = None
        dependencies = payload.get("dependencies")
        if isinstance(dependencies, list):
            payload["dependencies"] = [
                str(dep).strip()
                for dep in dependencies
                if str(dep).strip()
            ]
        else:
            payload["dependencies"] = []
        novelty_basis = payload.get("novelty_basis")
        if isinstance(novelty_basis, list):
            payload["novelty_basis"] = [str(item) for item in novelty_basis]
        else:
            payload["novelty_basis"] = []
        causal_links = payload.get("causal_links")
        if isinstance(causal_links, list):
            normalized_links: List[Dict[str, Any]] = []
            for link in causal_links:
                if isinstance(link, dict):
                    normalized_links.append(dict(link))
            payload["causal_links"] = normalized_links
        else:
            payload["causal_links"] = []
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            payload["metadata"] = dict(metadata)
        else:
            payload["metadata"] = {}
        entry = RefinementLedgerEntry(**payload)

    ledger = state.setdefault("refinement_ledger", [])
    ledger.append(entry)


def apply_prp_transition(
    state: QuadraCodeState,
    target_state: PRPState,
    *,
    exhaustion_mode: ExhaustionMode | str | None = None,
    human_clone_triggered: bool = False,
    telemetry_callback: Callable[[str, Dict[str, Any]], None] | None = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """Apply a PRP transition with guard enforcement and telemetry logging."""

    current_state_value = state.get("prp_state", PRP_STATE_MACHINE.initial_state)
    if isinstance(current_state_value, str):
        try:
            current_state = PRPState(current_state_value)
        except ValueError:
            current_state = PRP_STATE_MACHINE.initial_state
    elif isinstance(current_state_value, PRPState):
        current_state = current_state_value
    else:
        current_state = PRP_STATE_MACHINE.initial_state

    exhaustion_value = exhaustion_mode or state.get("exhaustion_mode", ExhaustionMode.NONE)
    if isinstance(exhaustion_value, str):
        try:
            exhaustion = ExhaustionMode(exhaustion_value)
        except ValueError:
            exhaustion = ExhaustionMode.NONE
    elif isinstance(exhaustion_value, ExhaustionMode):
        exhaustion = exhaustion_value
    else:
        exhaustion = ExhaustionMode.NONE

    def _record(event: str, payload: Dict[str, Any]) -> None:
        entry = {"event": event, **payload}
        metrics_log = state.setdefault("metrics_log", [])
        metrics_log.append(entry)
        telemetry_log = state.setdefault("prp_telemetry", [])
        telemetry_log.append({"event": event, "payload": payload})
        if telemetry_callback:
            telemetry_callback(event, payload)

    try:
        rule = PRP_STATE_MACHINE.validate_transition(
            current_state,
            target_state,
            exhaustion_mode=exhaustion,
            human_clone_triggered=human_clone_triggered,
        )
    except PRPInvalidTransitionError as exc:
        _record(
            "prp_invalid_transition",
            {
                **exc.telemetry,
                "applied": False,
            },
        )
        if strict:
            raise
        return {}

    updates: Dict[str, Any] = {
        "prp_state": target_state,
        "is_in_prp": True,
    }

    invariants = state.setdefault("invariants", {})
    if isinstance(invariants, dict):
        if target_state == PRPState.EXECUTE and current_state == PRPState.HYPOTHESIZE:
            invariants["skepticism_gate_satisfied"] = False
        elif target_state == PRPState.HYPOTHESIZE:
            invariants["skepticism_gate_satisfied"] = False

    if current_state == PRPState.PROPOSE and target_state == PRPState.HYPOTHESIZE:
        updates["prp_cycle_count"] = int(state.get("prp_cycle_count", 0)) + 1
        # HumanClone rejection path requires a test before concluding/proposing again
        mark_rejection_requires_tests(state)

    state.update(updates)

    prp_event = {
        "applied": True,
        "from_state": current_state.value,
        "to_state": target_state.value,
        "exhaustion_mode": exhaustion.value,
        "human_clone_triggered": human_clone_triggered,
        "description": rule.description,
        "requires_human_clone": rule.requires_human_clone,
    }
    telemetry_log = state.setdefault("prp_telemetry", [])
    if isinstance(telemetry_log, list):
        telemetry_log.append({"event": "prp_transition", "payload": prp_event})

    observer = get_meta_observer()
    observer.publish_cycle_snapshot(state, source="prp_transition")
    get_time_travel_recorder().log_transition(
        state,
        event="prp_transition",
        payload={
            "from_state": current_state.value,
            "to_state": target_state.value,
            "description": rule.description,
            "requires_human_clone": rule.requires_human_clone,
        },
        state_update=updates,
    )

    # Evaluate invariants on transition and record violations (non-fatal)
    violations = check_transition_invariants(
        state,
        from_state=current_state.value,
        to_state=target_state.value,
    )
    if violations:
        telemetry_log = state.setdefault("prp_telemetry", [])
        if isinstance(telemetry_log, list):
            for v in violations:
                telemetry_log.append({"event": "invariant_violation", "payload": v})

    return updates

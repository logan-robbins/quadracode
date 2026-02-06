"""
Core data structures and state management for the Quadracode runtime.

This module defines the TypedDicts, Pydantic models, and enumerations that constitute
the `QuadraCodeState`, the central state object passed through the LangGraph workflow.
It includes definitions for:

- `RuntimeState`: Basic execution state for autonomous operation.
- `ContextEngineState`: State related to context management, memory, and progressive loading.
- `QuadraCodeState`: The comprehensive orchestrator state, including meta-cognitive
  signals from the Perpetual Refinement Protocol (PRP).
- `ExhaustionMode`: Enumeration of conditions that can trigger a refinement cycle.
- `PRPState`: Operational states within the PRP finite state machine.
- `PRPStateMachine`: The logic governing valid transitions between PRP states.
- `RefinementLedgerEntry`: A structured record of a single PRP cycle.

Utility functions are provided for state serialization/deserialization, initialization,
and state modification (e.g., recording test results, applying PRP transitions).
These functions ensure that state changes are observable, auditable through time-travel
logging, and compliant with runtime invariants.
"""
from __future__ import annotations

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
    """
    Represents a single milestone in an autonomous task.

    This structure tracks the progress, status, and summary of a discrete
    unit of work within a larger autonomous goal, facilitating both machine
    and human-readable progress monitoring.

    Attributes:
        milestone: A unique integer identifier for the milestone.
        title: An optional, human-readable title for the milestone.
        status: The current state of the milestone (e.g., "pending", "in_progress").
        summary: A brief description of the milestone's outcome or current state.
        next_steps: A list of proposed actions to advance from this milestone.
        updated_at: An ISO 8601 timestamp of the last update.
    """

    milestone: int
    title: Optional[str]
    status: Literal["pending", "in_progress", "complete", "blocked"]
    summary: Optional[str]
    next_steps: List[str]
    updated_at: Optional[str]


class AutonomousErrorRecord(TypedDict, total=False):
    """
    A structured record of an error encountered during autonomous operation.

    This record captures essential details about an error, including its type,
    attempts made at recovery, and whether it was escalated or resolved. This
    information is critical for debugging, learning from failures, and improving
    the system's resilience.

    Attributes:
        error_type: The class or category of the error (e.g., "ToolExecutionError").
        description: A human-readable description of what went wrong.
        recovery_attempts: A list of actions taken to try and recover from the error.
        escalated: A boolean flag indicating if the error required human intervention.
        resolved: A boolean flag indicating if the error was successfully handled.
        timestamp: An ISO 8601 timestamp of when the error occurred.
    """

    error_type: str
    description: str
    recovery_attempts: List[str]
    escalated: bool
    resolved: bool
    timestamp: Optional[str]


class _RuntimeStateRequired(TypedDict):
    """Internal base class defining fields required by all runtime states."""
    messages: Annotated[list[AnyMessage], add_messages]


class RuntimeState(_RuntimeStateRequired, total=False):
    """
    Base TypedDict for core runtime state tracking in autonomous mode.

    This structure holds fundamental information about the execution of an
    autonomous task, such as its goal, current phase, and operational limits.
    It serves as the foundation upon which more specialized states like
    `ContextEngineState` and `QuadraCodeState` are built.

    Attributes:
        messages: The sequence of messages forming the conversation history.
        autonomous_mode: A flag indicating if the system is operating autonomously.
        task_goal: The high-level objective defined for the current task.
        current_phase: The name of the current operational phase (e.g., "planning", "execution").
        iteration_count: The number of cycles or iterations completed.
        milestones: A list of `AutonomousMilestone` records tracking progress.
        error_history: A list of `AutonomousErrorRecord` instances.
        autonomous_started_at: Timestamp when autonomous mode was initiated.
        last_iteration_at: Timestamp of the last completed iteration.
        iteration_limit_triggered: Flag indicating if the iteration limit was reached.
        runtime_limit_triggered: Flag indicating if the total runtime limit was reached.
        autonomous_routing: A dictionary containing dynamic routing decisions.
        autonomous_settings: Configuration settings for the autonomous session.
        thread_id: The identifier for the current execution thread.
        workspace: A dictionary representing the state of the agent's workspace.
    """
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
    """
    Metadata for a single, manageable unit of context within the context window.

    This structure represents a chunk of information (e.g., a file snippet, a
    tool output) that the context engine manages. It includes metadata used for
    prioritization, decay, compression, and potential externalization to long-term
    memory, forming the core of the ACE/MemAct context management strategy.

    Attributes:
        id: A unique identifier for the context segment.
        content: The actual text or data of the segment.
        type: The category of the content (e.g., "file", "tool_output", "user_message").
        priority: An integer score indicating the segment's importance.
        token_count: The number of tokens in the content.
        timestamp: The ISO 8601 timestamp of when the segment was created or last accessed.
        decay_rate: A float indicating how quickly the segment's priority should decrease over time.
        compression_eligible: A boolean flag indicating if the segment can be summarized or compressed.
        restorable_reference: An optional reference (e.g., file path and line numbers)
                              allowing the full content to be reloaded from an external source.
    """

    id: str
    content: str
    type: str
    priority: int
    token_count: int
    timestamp: str
    decay_rate: float
    compression_eligible: bool
    restorable_reference: Optional[str]


# ============================================================================
# Context Segment Helper Functions
# ============================================================================

def get_segment(state: "QuadraCodeState", segment_id: str) -> Optional[ContextSegment]:
    """
    Retrieve a context segment by its ID.
    
    Args:
        state: The current QuadraCodeState.
        segment_id: The unique identifier of the segment to retrieve.
    
    Returns:
        The ContextSegment if found, otherwise None.
    """
    segments = state.get("context_segments", [])
    return next((s for s in segments if s.get("id") == segment_id), None)


def get_segment_content(state: "QuadraCodeState", segment_id: str) -> str:
    """
    Retrieve the content of a context segment by its ID.
    
    Args:
        state: The current QuadraCodeState.
        segment_id: The unique identifier of the segment.
    
    Returns:
        The segment's content string, or empty string if not found.
    """
    segment = get_segment(state, segment_id)
    return segment.get("content", "") if segment else ""


def upsert_segment(state: "QuadraCodeState", segment: ContextSegment) -> None:
    """
    Insert or update a context segment in the state.
    
    If a segment with the same ID exists, it is replaced. Otherwise, the segment
    is appended to the context_segments list.
    
    Args:
        state: The current QuadraCodeState.
        segment: The ContextSegment to insert or update.
    """
    segments = state.get("context_segments", [])
    existing_idx = next(
        (idx for idx, s in enumerate(segments) if s.get("id") == segment["id"]),
        None
    )
    
    if existing_idx is not None:
        segments[existing_idx] = segment
    else:
        segments.append(segment)
    
    state["context_segments"] = segments


def remove_segment(state: "QuadraCodeState", segment_id: str) -> bool:
    """
    Remove a context segment from the state by its ID.
    
    Args:
        state: The current QuadraCodeState.
        segment_id: The unique identifier of the segment to remove.
    
    Returns:
        True if the segment was found and removed, False otherwise.
    """
    segments = state.get("context_segments", [])
    initial_len = len(segments)
    state["context_segments"] = [s for s in segments if s.get("id") != segment_id]
    return len(state["context_segments"]) < initial_len


class MemoryCheckpoint(TypedDict):
    """
    Represents a persisted, restorable snapshot of the agent's context state.

    A memory checkpoint is a point-in-time capture of the agent's working memory
    and context, saved to an external store. This allows for long-term persistence
    of state across sessions and provides a mechanism for time-travel debugging
    or restoring a previous state.

    Attributes:
        checkpoint_id: A unique identifier for the checkpoint.
        timestamp: The ISO 8601 timestamp when the checkpoint was created.
        milestone: An optional integer linking the checkpoint to an `AutonomousMilestone`.
        summary: A human-readable summary of the agent's state at the time of the checkpoint.
        full_context_path: The file path or URI where the full serialized context is stored.
        token_count: The total number of tokens in the captured context.
        quality_score: A metric evaluating the coherence and relevance of the captured context.
    """

    checkpoint_id: str
    timestamp: str
    milestone: Optional[int]
    summary: str
    full_context_path: str
    token_count: int
    quality_score: float


class ExhaustionMode(str, Enum):
    """
    Enumeration of triggers that signal the need for cognitive refinement.

    Exhaustion modes are specific conditions detected by the runtime that indicate
    a simple reactive loop is insufficient and a more deliberate, meta-cognitive
    approach is needed. These triggers activate the Perpetual Refinement Protocol (PRP),
    shifting the agent from execution to hypothesis and refinement.

    Attributes:
        NONE: No exhaustion is currently active.
        CONTEXT_SATURATION: The context window is full, preventing new information from being added.
        RETRY_DEPLETION: An action has failed repeatedly, exceeding its retry limit.
        TOOL_BACKPRESSURE: A tool is consistently failing or indicating it is overloaded.
        LLM_STOP: The language model has produced a stop sequence, indicating it cannot proceed.
        TEST_FAILURE: A validation or regression test has failed, indicating the current approach is flawed.
        HYPOTHESIS_EXHAUSTED: The current line of reasoning has failed to produce a viable solution.
        PREDICTED_EXHAUSTION: A predictive model anticipates that the current strategy is likely to fail.
    """

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
    """

    A structured log entry detailing a single cycle of the Perpetual Refinement Protocol (PRP).

    This model captures the complete state of a refinement loop, from the initial
    hypothesis to the final outcome. It includes metadata about the strategy employed,
    the novelty of the approach, and causal links to previous events. The ledger
    provides a detailed, auditable history of the agent's meta-cognitive
    problem-solving process.

    Attributes:
        cycle_id: A unique identifier for this refinement cycle.
        timestamp: The start time of the cycle.
        hypothesis: The proposed idea or solution being investigated.
        status: The current status of the cycle (e.g., "in_progress", "completed", "failed").
        outcome_summary: A concise summary of the cycle's result.
        exhaustion_trigger: The `ExhaustionMode` that initiated this PRP cycle.
        test_results: A dictionary containing outcomes from validation tests.
        strategy: The name of the refinement strategy used (e.g., "backtracking", "analogy").
        novelty_score: A float measuring the newness of the hypothesis compared to previous attempts.
        novelty_basis: A list of identifiers or reasons justifying the novelty score.
        dependencies: A list of cycle_ids or other dependencies for this cycle.
        predicted_success_probability: A score predicting the likelihood of the hypothesis succeeding.
        causal_links: Structured data linking this cycle to preceding events or states.
        metadata: An open dictionary for any other relevant telemetry.
    """

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
        """
        Generates a compact, single-line string summary of the ledger entry.

        This summary is designed for inclusion in prompts or concise logging,
        providing the most critical information about the cycle's identity,
        status, hypothesis, and outcome in a dense format.

        Returns:
            A pipe-separated string summarizing the entry.
        """

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
        """Internal helper to create a compact summary of test results."""
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
    """
    Enumerates the distinct operational states within the Perpetual Refinement Protocol (PRP) FSM.

    Each state represents a specific phase of the meta-cognitive loop, guiding the
    agent through a structured process of hypothesis, execution, testing, and conclusion.

    Attributes:
        HYPOTHESIZE: The state for generating a new hypothesis or refining an existing one.
        EXECUTE: The state for planning and executing actions based on the current hypothesis.
        TEST: The state for validating the outcome of the execution phase against defined criteria.
        CONCLUDE: The state for synthesizing the results and determining the success of the hypothesis.
        PROPOSE: The state for packaging the conclusion into a formal proposal for supervisor review.
    """

    HYPOTHESIZE = "hypothesize"
    EXECUTE = "execute"
    TEST = "test"
    CONCLUDE = "conclude"
    PROPOSE = "propose"


class PRPTransition(BaseModel):
    """
    Defines a single, guarded transition rule within the PRP finite state machine.

    This model specifies a valid move from a source state to a target state, along
    with conditions that must be met for the transition to be allowed. These guards
    can be based on the current exhaustion mode or other runtime signals, ensuring

    the PRP state machine operates in a controlled and predictable manner.

    Attributes:
        source: The starting `PRPState` for this transition.
        target: The destination `PRPState`.
        description: A human-readable explanation of the transition's purpose.
        allow_if_exhaustion_in: A set of `ExhaustionMode`s under which this transition is permitted.
                                If None, it's allowed for any exhaustion mode (unless blocked).
        block_if_exhaustion_in: A set of `ExhaustionMode`s that explicitly forbid this transition.
        requires_supervisor: A boolean flag indicating if this transition requires
                              an action or approval from the supervisor agent.
    """

    source: PRPState
    target: PRPState
    description: str = ""
    allow_if_exhaustion_in: set[ExhaustionMode] | None = Field(default=None)
    block_if_exhaustion_in: set[ExhaustionMode] | None = Field(default=None)
    requires_supervisor: bool = False


class PRPInvalidTransitionError(RuntimeError):
    """
    Exception raised when a requested PRP state transition violates a defined guard.

    This error is thrown by the `PRPStateMachine` when an attempt is made to move
    between states in a way that is not permitted by the configured transition
    rules (e.g., blocked by the current exhaustion mode). It captures detailed
    telemetry about the failed transition attempt for debugging and analysis.

    Attributes:
        telemetry: A dictionary containing details of the invalid transition,
                   including source/target states, exhaustion mode, and the reason for failure.
    """

    def __init__(
        self,
        *,
        source: PRPState,
        target: PRPState,
        exhaustion_mode: ExhaustionMode,
        supervisor_triggered: bool,
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
            "supervisor_triggered": supervisor_triggered,
            "reason": reason,
            "description": description,
        }


class PRPStateMachine:
    """
    A finite-state automaton that governs the Perpetual Refinement Protocol (PRP) control loop.

    This class manages the lifecycle of the PRP by defining the valid states, the
    transitions between them, and the guards that must be satisfied for a
    transition to occur. It provides a `validate_transition` method to enforce
    these rules, ensuring that the agent's meta-cognitive process follows a
    structured and robust path.

    The state machine is initialized with a set of `PRPTransition` rules and a
    defined initial state.

    Attributes:
        initial_state: The `PRPState` where the machine begins.
    """

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
        """
        Retrieves the transition rule for a given source and target state.

        Args:
            source: The starting `PRPState`.
            target: The destination `PRPState`.

        Returns:
            The corresponding `PRPTransition` object if a rule exists, otherwise `None`.
        """
        return self._graph.get(source, {}).get(target)

    def validate_transition(
        self,
        source: PRPState,
        target: PRPState,
        *,
        exhaustion_mode: ExhaustionMode,
        supervisor_triggered: bool,
    ) -> PRPTransition:
        """
        Checks if a transition is valid based on current runtime conditions.

        This method enforces the guards defined in the `PRPTransition` rules.
        It will raise a `PRPInvalidTransitionError` if the requested transition
        is not defined, is blocked by the current exhaustion mode, or fails to
        meet other preconditions like `requires_supervisor`.

        Args:
            source: The current `PRPState`.
            target: The desired next `PRPState`.
            exhaustion_mode: The active `ExhaustionMode`.
            supervisor_triggered: A flag indicating if a supervisor action has occurred.

        Returns:
            The `PRPTransition` object if the transition is valid.

        Raises:
            PRPInvalidTransitionError: If the transition violates any defined guards.
        """
        transition = self.get_transition(source, target)
        if transition is None:
            raise PRPInvalidTransitionError(
                source=source,
                target=target,
                exhaustion_mode=exhaustion_mode,
                supervisor_triggered=supervisor_triggered,
                reason="transition_not_defined",
            )

        if transition.allow_if_exhaustion_in is not None and (
            exhaustion_mode not in transition.allow_if_exhaustion_in
        ):
            raise PRPInvalidTransitionError(
                source=source,
                target=target,
                exhaustion_mode=exhaustion_mode,
                supervisor_triggered=supervisor_triggered,
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
                supervisor_triggered=supervisor_triggered,
                reason="exhaustion_blocked",
                description=transition.description,
            )

        if transition.requires_supervisor and not supervisor_triggered:
            raise PRPInvalidTransitionError(
                source=source,
                target=target,
                exhaustion_mode=exhaustion_mode,
                supervisor_triggered=supervisor_triggered,
                reason="supervisor_required",
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
        description="Package conclusions into a proposal for supervisor review.",
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
        requires_supervisor=True,
    ),
]


PRP_STATE_MACHINE = PRPStateMachine(DEFAULT_PRP_TRANSITIONS)


def add_context_segments(
    left: Optional[List[ContextSegment]], right: Optional[List[ContextSegment]]
) -> List[ContextSegment]:
    """
    Reducer that merges context segments by ID while preserving stable order.

    LangGraph applies reducers to combine partial state updates emitted by nodes.
    Without a reducer, list fields are overwritten wholesale, which causes data
    loss when upstream nodes emit only partial updates. This reducer ensures that
    new segments replace prior ones with the same ID, while segments with new IDs
    are appended in the order they are introduced.
    """
    # Ensure left is a list
    current = list(left) if left else []
    
    # If no update, return current state
    if not right:
        return current

    # Create index of existing items
    index = {item["id"]: i for i, item in enumerate(current)}
    
    for segment in right:
        segment_id = segment.get("id")
        if not segment_id:
            continue
            
        if segment_id in index:
            # Update existing
            current[index[segment_id]] = segment
        else:
            # Append new
            index[segment_id] = len(current)
            current.append(segment)

    return current


def set_context_segments(
    left: Optional[List[ContextSegment]], right: Optional[List[ContextSegment]]
) -> List[ContextSegment]:
    """
    Reducer that replaces the context_segments list with the new value if provided,
    to ensure full list updates from nodes override the previous state.
    """
    return right if right is not None else (left or [])


class ContextEngineState(RuntimeState):
    """
    Extends `RuntimeState` with detailed tracking for advanced context management.

    This state object is used by the context engineering nodes of the graph to
    manage the agent's working memory, external memory, and the progressive
    loading of context. It includes fields for tracking context window usage,
    quality scores, memory checkpoints, and the various components of the
    ACE (Adaptive Contextual Engagement) and MemAct systems.

    Attributes:
        context_window_used: The number of tokens currently in the context window.
        context_window_max: The maximum capacity of the context window.
        context_quality_score: A composite score evaluating the relevance and coherence of the current context.
        context_segments: A list of `ContextSegment` objects - the single source of truth for all in-context data.
        external_memory_index: An index mapping keys to references in external storage (e.g., file system).
        memory_checkpoints: A list of `MemoryCheckpoint` snapshots.
        pending_context: A queue of context items waiting to be loaded.
        context_hierarchy: A structure defining priority and relationships between context elements.
        prefetch_queue: A queue for speculative loading of context.
        context_playbook: A set of rules or strategies for managing context.
        reflection_log: A log of the context engine's self-reflection and adjustments.
        curation_rules: A list of rules for automated context curation.
        compression_ratio: A metric for how effectively context is being compressed.
        retrieval_accuracy: A metric for the accuracy of information retrieval from memory.
        attention_distribution: A map showing how attention is distributed across context segments.
        context_quality_components: A breakdown of the factors contributing to the quality score.
        metrics_log: A time-series log of context-related metrics.
        governor_plan: The plan generated by the context governor for prompt construction.
        governor_prompt_outline: A structured outline of the prompt to be built.
        skills_catalog: A catalog of available skills or tools for context manipulation.
        active_skills_metadata: Metadata for the currently active skills.
        loaded_skills: The skill implementations currently loaded into memory.
        last_curation_summary: A summary of the last context curation action.
        recent_loads: A log of recently loaded context segments.
        recent_externalizations: A log of recently externalized context segments.
        llm_stop_detected: Flag indicating if the LLM has signaled a stop condition (exhaustion).
        llm_resume_hint: Flag indicating the LLM should be hinted to resume from a stop.
    system_prompt_addendum: Supplemental system prompt text injected after reset.
    context_reset_count: Number of context reset events executed.
    context_reset_log: Log of context reset metadata.
    last_context_reset: Latest context reset metadata.
    """

    # Context Management
    context_window_used: int
    context_window_max: int
    context_quality_score: float

    # Context Segments (Single Source of Truth)
    context_segments: Annotated[List[ContextSegment], set_context_segments]

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
    recent_compressions: List[Dict[str, Any]]
    last_compression_event: Dict[str, Any]
    
    # LLM Stop/Resume Detection (for exhaustion handling)
    llm_stop_detected: bool
    llm_resume_hint: bool

    # Context reset metadata
    system_prompt_addendum: str
    context_reset_count: int
    context_reset_log: List[Dict[str, Any]]
    last_context_reset: Dict[str, Any]


class QuadraCodeState(ContextEngineState, total=False):
    """
    The primary, comprehensive state object for the Quadracode orchestrator.

    This `TypedDict` aggregates all other state definitions (`RuntimeState`,
    `ContextEngineState`) and adds fields specifically for the meta-cognitive
    and deliberative capabilities of the system, centered around the Perpetual
    Refinement Protocol (PRP). It is the single source of truth that is passed
    between all nodes in the LangGraph workflow.

    Attributes:
        is_in_prp: A flag indicating if the system is currently in a PRP cycle.
        prp_cycle_count: The number of PRP cycles completed.
        prp_state: The current `PRPState` in the state machine.
        refinement_ledger: A list of `RefinementLedgerEntry` objects, logging the history of PRP cycles.
        exhaustion_mode: The current `ExhaustionMode` that triggered or is active within the PRP.
        exhaustion_probability: A predicted probability of entering an exhaustion state.
        exhaustion_recovery_log: A log of attempts to recover from exhaustion states.
        refinement_memory_block: A synthesized block of text summarizing recent refinements for prompt injection.
        prp_telemetry: A detailed log of events and metrics related to the PRP.
        supervisor_requirements: A list of conditions or information needed from the supervisor agent.
        supervisor_trigger: Data related to the event that triggered a need for supervisor intervention.
        last_test_suite_result: The result of the last full test suite run.
        last_property_test_result: The result of the last property-based test.
        property_test_results: A list of recent property-based test outcomes.
        debugger_agents: A list of specialized agents spawned for debugging purposes.
        workspace_snapshots: A history of `WorkspaceSnapshotRecord`s.
        workspace_validation: State concerning the integrity and validation of the workspace.
        critique_backlog: A list of critiques or suggested improvements to be addressed.
        hypothesis_cycle_metrics: Metrics specifically tracking the performance of hypothesis generation.
        time_travel_log: A log of state transitions for debugging and replay.
        invariants: A dictionary tracking the status of system invariants.
        autonomy_counters: Counters for events like false stops and skepticism challenges.
        deliberative_plan: The output of a deliberative planning process.
        deliberative_intermediate_states: Intermediate states generated during planning.
        deliberative_synopsis: A summary of the deliberative plan.
        counterfactual_register: A log of counterfactual reasoning explorations.
        causal_graph_snapshot: A snapshot of the system's causal model.
        planning_uncertainty: A score representing uncertainty in the current plan.
        planning_success_probability: The predicted probability of the current plan succeeding.
        episodic_memory: Memory of specific events or sequences of actions.
        semantic_memory: Abstracted knowledge and facts derived from experience.
        memory_consolidation_log: A log of operations that consolidate episodic into semantic memory.
        memory_guidance: Directives for how memory should be utilized in the current context.
    """

    # Explicitly re-declare with annotation to ensure visibility in subclass
    context_segments: Annotated[List[ContextSegment], set_context_segments]

    is_in_prp: bool
    prp_cycle_count: int
    prp_state: PRPState
    refinement_ledger: List[RefinementLedgerEntry]
    exhaustion_mode: ExhaustionMode
    exhaustion_probability: float
    exhaustion_recovery_log: List[Dict[str, Any]]
    refinement_memory_block: str
    prp_telemetry: List[Dict[str, Any]]
    supervisor_requirements: List[str]
    supervisor_trigger: Dict[str, Any]
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
    """
    Factory function to create a new, fully-initialized `QuadraCodeState`.

    Initializes the comprehensive `QuadraCodeState` with sensible default values for
    all its fields, including context management, PRP state, memory, and telemetry
    logs. This ensures that a new graph execution starts from a clean, predictable
    state.

    Args:
        context_window_max: The maximum context window size to configure for this state.

    Returns:
        A `QuadraCodeState` dictionary populated with default values.
    """

    return cast(
        QuadraCodeState,
        {
            "messages": [],
            "context_window_used": 0,
            "context_window_max": context_window_max,
            "context_quality_score": 0.0,
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
            "recent_compressions": [],
            "last_compression_event": {},
            "llm_stop_detected": False,
            "llm_resume_hint": False,
            "system_prompt_addendum": "",
            "context_reset_count": 0,
            "context_reset_log": [],
            "last_context_reset": {},
            "is_in_prp": False,
            "prp_cycle_count": 0,
            "prp_state": PRP_STATE_MACHINE.initial_state,
            "refinement_ledger": [],
            "exhaustion_mode": ExhaustionMode.NONE,
            "exhaustion_probability": 0.0,
            "exhaustion_recovery_log": [],
            "refinement_memory_block": "",
            "prp_telemetry": [],
            "supervisor_requirements": [],
            "supervisor_trigger": {},
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
    """
    Converts a `QuadraCodeState` object into a JSON-serializable dictionary.

    This function handles the conversion of complex types within the state,
    such as `AnyMessage`, `RefinementLedgerEntry`, `WorkspaceSnapshotRecord`,
    and enums (`ExhaustionMode`, `PRPState`), into formats that can be
    readily stored in JSON files or transmitted over a network.

    Args:
        state: The `QuadraCodeState` object to serialize.

    Returns:
        A dictionary containing only JSON-compatible types.
    """

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
    """
    Rehydrates a `QuadraCodeState` object from a JSON-serializable dictionary.

    This function performs the reverse of `serialize_context_engine_state`,
    reconstructing the rich object types (`AnyMessage`, `RefinementLedgerEntry`, enums, etc.)
    from their serialized string or dictionary representations. It includes robust
    handling of missing keys and type mismatches to ensure backward compatibility
    with older state formats.

    Args:
        payload: A dictionary containing the serialized state.

    Returns:
        A fully-rehydrated `QuadraCodeState` object.
    """

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
    if "system_prompt_addendum" not in state or not isinstance(state.get("system_prompt_addendum"), str):
        state["system_prompt_addendum"] = ""
    if "context_reset_count" not in state or not isinstance(state.get("context_reset_count"), int):
        state["context_reset_count"] = 0
    if "context_reset_log" not in state or not isinstance(state.get("context_reset_log"), list):
        state["context_reset_log"] = []
    if "last_context_reset" not in state or not isinstance(state.get("last_context_reset"), dict):
        state["last_context_reset"] = {}
    return cast(QuadraCodeState, state)


def _latest_ledger_entry(state: QuadraCodeState) -> RefinementLedgerEntry | None:
    """
    Safely retrieves the most recent entry from the refinement ledger.

    This helper function accesses the `refinement_ledger` list from the state,
    handles cases where it might be empty or contain unhydrated dictionaries,
    and returns the last entry as a `RefinementLedgerEntry` object if possible.

    Args:
        state: The current `QuadraCodeState`.

    Returns:
        The latest `RefinementLedgerEntry` or `None` if the ledger is empty or invalid.
    """
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
    """
    Determines the identifier for the current or next PRP refinement cycle.

    This is used for tagging telemetry and logs. It first attempts to get the
    `cycle_id` from the latest entry in the refinement ledger. If no entry
    exists, it computes a new ID based on the `prp_cycle_count`.

    Args:
        state: The current `QuadraCodeState`.

    Returns:
        A string identifier for the active refinement cycle.
    """

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
    """
    Ensures that the `test_results` attribute of a ledger entry is a dictionary.

    This utility handles cases where `test_results` might be `None` or an older,
    non-dictionary format. It initializes or migrates the attribute to a
    dictionary structure, making it safe to add new test results.

    Args:
        entry: The `RefinementLedgerEntry` to modify.

    Returns:
        The `test_results` dictionary from the entry.
    """
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
    """
    Appends a structured entry to the exhaustion recovery log for test failures.

    Args:
        state: The `QuadraCodeState` to be updated.
        tool: The name of the tool or process that reported the failure.
        message: A description of the failure.
        extra: Optional dictionary with additional context about the failure.
    """
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
    """
    Initializes the autonomy counters in the state if they don't exist.

    Ensures that the `autonomy_counters` dictionary and its nested keys for
    tracking events like false stops and skepticism challenges are present
    and correctly typed.

    Args:
        state: The `QuadraCodeState` to be checked and updated.

    Returns:
        The ensured autonomy counters dictionary.
    """
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
    """
    Records the detection of a potential "false stop" event in autonomous mode.

    A false stop occurs when the agent incorrectly concludes its task is complete.
    This function increments the relevant counters (`false_stop_events`,
    `false_stop_pending`) and logs detailed telemetry about the event for
    observability and potential mitigation.

    Args:
        state: The `QuadraCodeState` to update.
        reason: A description of why a false stop is suspected.
        stage: The operational stage where the event was detected (e.g., "test", "conclusion").
        evidence: A dictionary of data supporting the detection.

    Returns:
        A dictionary containing the payload of the logged telemetry event.
    """

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
    """
    Marks one pending false-stop event as mitigated or resolved.

    When the agent successfully proceeds past a point where a false stop was
    suspected, this function is called to decrement the `false_stop_pending`
    counter and increment the `false_stop_mitigated` counter.

    Args:
        state: The `QuadraCodeState` to update.
        stage: The operational stage where the mitigation occurred.
        evidence: A dictionary of data demonstrating the successful continuation.

    Returns:
        `True` if a pending event was resolved, `False` otherwise.
    """

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
    """
    Logs a "skepticism challenge" event and updates system invariants.

    A skepticism challenge is an internal mechanism where the system questions
    its own conclusions or plans, forcing a deeper validation. This function
    increments the `skepticism_challenges` counter and sets the
    `skepticism_gate_satisfied` invariant to true, potentially unlocking more
    rigorous behaviors.

    Args:
        state: The `QuadraCodeState` to update.
        source: The component or process that initiated the challenge.
        reason: The reason for the challenge.
        evidence: Supporting data for the challenge.

    Returns:
        The telemetry payload for the recorded event.
    """

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
    """
    Processes and records the results from a full test suite run.

    This function updates the `last_test_suite_result` in the state, adds the
    result to the current refinement ledger entry, and publishes telemetry.
    Crucially, if the tests fail, it sets the `exhaustion_mode` to
    `TEST_FAILURE`, triggering a PRP cycle to address the issue. It also
    clears the invariant that requires testing after a supervisor rejection.

    Args:
        state: The `QuadraCodeState` to update.
        result: A dictionary containing the structured results from the test suite.
    """

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
    """
    Processes and records the results from a property-based test.

    Similar to `record_test_suite_result`, this function updates the state with
    the latest property test outcome, logs it to the refinement ledger, and
    publishes telemetry. A failed property test also sets the `exhaustion_mode`
    to `TEST_FAILURE`, initiating a refinement cycle. It clears the post-rejection
    testing requirement.

    Args:
        state: The `QuadraCodeState` to update.
        payload: A dictionary containing the structured results of the property test.
    """

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
    """
    Appends a new entry to the refinement ledger in the state.

    This function handles the addition of a new `RefinementLedgerEntry`. It
    can accept either a pre-constructed `RefinementLedgerEntry` object or a
    dictionary, which it will normalize and validate before converting it into
    the Pydantic model. This ensures data consistency within the ledger.

    Args:
        state: The `QuadraCodeState` to be updated.
        entry: The ledger entry to add, as either a model instance or a dictionary.
    """

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
    supervisor_triggered: bool = False,
    telemetry_callback: Callable[[str, Dict[str, Any]], None] | None = None,
    strict: bool = False,
) -> Dict[str, Any]:
    """
    Executes a state transition within the PRP state machine, enforcing all guards.

    This is the primary function for changing the `prp_state`. It validates the
    requested transition against the `PRPStateMachine` rules based on the current
    state and exhaustion mode. If valid, it updates the state, logs extensive
    telemetry, publishes observability events, and checks for any invariant
    violations post-transition.

    Args:
        state: The `QuadraCodeState` to be updated.
        target_state: The desired destination `PRPState`.
        exhaustion_mode: The current `ExhaustionMode` to validate against. If not
                         provided, it's read from the state.
        supervisor_triggered: Flag indicating if a supervisor action occurred.
        telemetry_callback: An optional callback for custom telemetry handling.
        strict: If `True`, raises `PRPInvalidTransitionError` on failure. If `False`,
                logs the error and returns gracefully.

    Returns:
        A dictionary of the state updates that were applied.
    """

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
            supervisor_triggered=supervisor_triggered,
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
        # Supervisor rejection path requires a test before concluding/proposing again
        mark_rejection_requires_tests(state)

    state.update(updates)

    prp_event = {
        "applied": True,
        "from_state": current_state.value,
        "to_state": target_state.value,
        "exhaustion_mode": exhaustion.value,
        "supervisor_triggered": supervisor_triggered,
        "description": rule.description,
        "requires_supervisor": rule.requires_supervisor,
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
            "requires_supervisor": rule.requires_supervisor,
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

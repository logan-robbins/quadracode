"""
This module implements the `ContextEngine`, the high-level coordinator for all 
context engineering flows in the Quadracode runtime.

The `ContextEngine` is a central component of the runtime, responsible for 
orchestrating the entire lifecycle of context management. It integrates various 
sub-components, such as the `ContextCurator`, `ContextScorer`, and 
`ProgressiveContextLoader`, to maintain a high-quality, relevant, and 
size-constrained context for the language models. It operates through a series of 
distinct processing stages (`pre_process`, `post_process`, `govern_context`), 
each of which is designed to be a stateless transformation of the main 
`QuadraCodeState`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import httpx

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, RemoveMessage, BaseMessage
from langchain_core.messages.utils import get_buffer_string

from ..config import ContextEngineConfig
from ..autonomous import process_autonomous_tool_response
from ..ledger import process_manage_refinement_ledger_tool_response
from ..exhaustion_predictor import ExhaustionPredictor
from ..deliberative import DeliberativePlanner, DeliberativePlanArtifacts
from ..context_engine_logging import log_context_compression
from ..state import (
    ContextSegment,
    ExhaustionMode,
    PRPState,
    QuadraCodeState,
    RefinementLedgerEntry,
    flag_false_stop_event,
    apply_prp_transition,
    make_initial_context_engine_state,
    record_skepticism_challenge,
    record_property_test_result,
    record_test_suite_result,
)
from ..long_term_memory import update_memory_guidance
from ..metrics import ContextMetricsEmitter
from ..observability import get_meta_observer
from ..time_travel import get_time_travel_recorder
from ..invariants import mark_context_updated
from ..workspace_integrity import (
    capture_workspace_snapshot,
    validate_workspace_integrity,
)
from .context_curator import ContextCurator
from .context_operations import ContextOperation
from .context_reducer import ContextReducer
from .context_scorer import ContextScorer
from .progressive_loader import ProgressiveContextLoader


LOGGER = logging.getLogger(__name__)

@dataclass(slots=True)
class ReflectionResult:
    timestamp: str
    quality_score: float
    summary: str
    issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    focus_metric: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContextEngine:
    """
    Orchestrates the context engineering lifecycle.

    This class acts as the main entry point and coordinator for all context-related 
    operations. It manages the interactions between the various sub-components 
    of the context engine and ensures that the context is optimized and governed 
    at each stage of the processing loop.

    Attributes:
        config: The configuration for the context engine.
        curator: An instance of the `ContextCurator`.
        scorer: An instance of the `ContextScorer`.
        loader: An instance of the `ProgressiveContextLoader`.
        ... and other sub-components.
    """

    def __init__(self, config: ContextEngineConfig, system_prompt: str = ""):
        """
        Initializes the `ContextEngine`.

        Args:
            config: The configuration for the context engine.
            system_prompt: The static system prompt for the agent.
        """
        self.config = config
        self.system_prompt = system_prompt
        self.system_prompt_tokens = self._estimate_tokens(system_prompt)
        self.curator = ContextCurator(config)
        self.scorer = ContextScorer(config)
        self.loader = ProgressiveContextLoader(config)
        self.external_memory = _NoOpExternalMemory()
        self.metrics = ContextMetricsEmitter(config)
        self.reducer = ContextReducer(config)
        self.max_tool_payload_chars = config.max_tool_payload_chars
        self.governor_model = (config.governor_model or "heuristic").lower()
        self._governor_llm = None
        self._governor_lock = asyncio.Lock()
        self.exhaustion_predictor = ExhaustionPredictor()
        self._meta_observer = get_meta_observer()
        self.time_travel = get_time_travel_recorder()
        self.registry_url = os.environ.get(
            "AGENT_REGISTRY_URL",
            "http://agent-registry:8090",
        ).rstrip("/")
        self._hotpath_probe_timeout = float(
            os.environ.get("QUADRACODE_HOTPATH_PROBE_TIMEOUT", "3")
        )
        self.deliberative_planner = DeliberativePlanner()

    def _estimate_tokens(self, text: str) -> int:
        """Roughly estimates token count as 1 token per 4 characters."""
        if not text:
            return 0
        return int(len(text) / 4)

    def pre_process_sync(self, state: QuadraCodeState) -> Dict[str, Any]:
        """Synchronous wrapper for the `pre_process` method."""
        return asyncio.run(self.pre_process(state))

    def post_process_sync(self, state: QuadraCodeState) -> Dict[str, Any]:
        """Synchronous wrapper for the `post_process` method."""
        return asyncio.run(self.post_process(state))

    def govern_context_sync(self, state: QuadraCodeState) -> Dict[str, Any]:
        """Synchronous wrapper for the `govern_context` method."""
        return asyncio.run(self.govern_context(state))

    def handle_tool_response_sync(
        self, state: QuadraCodeState
    ) -> Dict[str, Any]:
        """Synchronous wrapper for the `handle_tool_response` method."""
        tool_messages = self._extract_tool_messages(state)
        return asyncio.run(self.handle_tool_response(state, tool_messages))

    async def pre_process(self, state: QuadraCodeState) -> Dict[str, Any]:
        """
        Executes the pre-processing stage of the context engineering pipeline.

        This stage is run before the main driver makes a decision. It is 
        responsible for evaluating the quality of the current context, running 
        curation and optimization routines if necessary, and loading any new 
        context that is required.

        Args:
            state: The current state of the system.

        Returns:
            The updated state after pre-processing.
        """
        # Create a working copy to avoid modifying the input state in place immediately
        # though shallow copy means mutable values are still shared, which is generally fine
        # for "replace" reducers, but we must be careful with "add_messages".
        state = state.copy()
        history_updates: dict[str, Any] = {}
        
        state = self._ensure_state_defaults(state)
        await self._enforce_hotpath_residency(state)
        quality_score = await self.scorer.evaluate(state)
        state["context_quality_score"] = quality_score
        
        # Compute context usage BEFORE checking for compression
        state = self._recompute_context_usage(state)
        
        # Get current dynamic token usage (messages + segments only, NOT system prompt)
        breakdown = state.get("_context_breakdown", {})
        message_tokens = breakdown.get("message_tokens", 0)
        segment_tokens = breakdown.get("segment_tokens", 0)
        current_dynamic_tokens = message_tokens + segment_tokens
        
        # Check if dynamic content exceeds optimal size (system prompt doesn't count)
        optimal_tokens = self.config.optimal_context_size
        
        if current_dynamic_tokens > optimal_tokens:
            # Dynamic content exceeds optimal - compress to fit within optimal_tokens
            available_dynamic_space = optimal_tokens
            
            # Compress conversation history and segments to fit
            history_updates = await self._manage_conversation_history(state, available_dynamic_space)
            if history_updates:
                # Apply message updates and re-calculate usage
                if "conversation_summary" in history_updates:
                    state["conversation_summary"] = history_updates["conversation_summary"]
                state["messages"].extend(history_updates.get("messages", []))
                state = self._recompute_context_usage(state)

            # After message compression, check if segment compression is still needed
            current_segment_tokens = state.get("_context_breakdown", {}).get("segment_tokens", 0)
            segment_budget = available_dynamic_space * (1 - self.config.message_budget_ratio)
            if current_segment_tokens > segment_budget:
                state = await self.curator.optimize(state, int(segment_budget))
                state = self._recompute_context_usage(state)
                await self._emit_curation_metrics(state, reason="overflow_control")
                await self._emit_externalization_metrics(state)

        state = await self.loader.prepare_context(state)
        state = self._recompute_context_usage(state)
        state = await self._enforce_limits(state)
        await self._emit_load_metrics(state)
        state = await self._update_exhaustion_mode(state, stage="pre_process")
        # Invariant: at least one context update per cycle
        mark_context_updated(state)
        
        await self.metrics.emit(
            state,
            "pre_process",
            {
                "quality_score": quality_score,
                "quality_components": state.get("context_quality_components", {}),
                "context_window_used": state.get("context_window_used", 0),
                "context_window_max": state.get("context_window_max", self.config.context_window_max),
                "exhaustion_mode": state.get("exhaustion_mode", ExhaustionMode.NONE).value,
                "exhaustion_probability": float(state.get("exhaustion_probability", 0.0)),
            },
        )
        await self._flush_prp_metrics(state)
        self.time_travel.log_stage(
            state,
            stage="pre_process",
            payload={
                "quality_score": quality_score,
                "context_window_used": state.get("context_window_used", 0),
                "context_segments": len(state.get("context_segments", [])),
            },
        )
        self._record_stage_observability(state, "pre_process")
        
        # IMPORTANT: Don't clear messages here - LangGraph's add_messages reducer 
        # will handle the RemoveMessage objects we added to the list.
        # The reducer will remove messages with matching IDs and keep the rest.
            
        return state

    async def post_process(self, state: QuadraCodeState) -> Dict[str, Any]:
        """
        Executes the post-processing stage of the context engineering pipeline.

        This stage is run after the main driver has made a decision. It is 
        responsible for reflecting on the decision, evolving the context 
        playbook, and performing any necessary cleanup of the context.

        Args:
            state: The current state of the system.

        Returns:
            The updated state after post-processing.
        """
        state = state.copy()
        state = self._ensure_state_defaults(state)
        reflection_payload = await self._reflect_on_decision(state)
        state["reflection_log"].append(
            {
                "timestamp": reflection_payload.timestamp,
                "summary": reflection_payload.summary,
                "issues": reflection_payload.issues,
                "recommendations": reflection_payload.recommendations,
                "focus_metric": reflection_payload.focus_metric,
                "quality_score": reflection_payload.quality_score,
            }
        )
        state = await self._evolve_playbook(state, reflection_payload)
        state = await self.curator.post_decision_curation(state)
        state = self._recompute_context_usage(state)

        if self._should_checkpoint(state):
            checkpoint_id = await self.external_memory.save_checkpoint(state)
            state["memory_checkpoints"].append(
                {
                    "checkpoint_id": checkpoint_id,
                    "timestamp": reflection_payload.timestamp,
                    "milestone": None,
                    "summary": "",
                    "full_context_path": "",
                    "token_count": state["context_window_used"],
                    "quality_score": state["context_quality_score"],
                }
            )

        state = await self._update_exhaustion_mode(state, stage="post_process")
        await self.metrics.emit(
            state,
            "post_process",
            {
                "quality_score": state.get("context_quality_score", 0.0),
                "focus_metric": reflection_payload.focus_metric,
                "issues": reflection_payload.issues,
                "recommendations": reflection_payload.recommendations,
                "context_window_used": state.get("context_window_used", 0),
                "exhaustion_mode": state.get("exhaustion_mode", ExhaustionMode.NONE).value,
                "exhaustion_probability": float(state.get("exhaustion_probability", 0.0)),
            },
        )
        self._apply_prp_transition(state, PRPState.PROPOSE)
        await self._flush_prp_metrics(state)
        self.time_travel.log_stage(
            state,
            stage="post_process",
            payload={
                "reflection_focus": reflection_payload.focus_metric,
                "issues": reflection_payload.issues,
                "recommendations": reflection_payload.recommendations,
            },
        )
        self._record_stage_observability(state, "post_process")
        
        # IMPORTANT: Remove messages to prevent duplication via add_messages reducer
        state.pop("messages", None)
        return state

    async def govern_context(self, state: QuadraCodeState) -> Dict[str, Any]:
        """
        Executes the context governance stage of the pipeline.

        This stage is responsible for generating a high-level plan for the 
        context, which includes actions for each context segment (e.g., retain, 
        compress, discard). It uses either a heuristic-based or an LLM-based 
        governor to create this plan.

        Args:
            state: The current state of the system.

        Returns:
            The updated state after context governance.
        """
        state = state.copy()
        state = self._ensure_state_defaults(state)
        plan = await self._generate_governor_plan(state)
        state = await self._apply_governor_plan(state, plan)
        deliberative = self.deliberative_planner.build_plan(state)
        self._store_deliberative_plan(state, deliberative)
        memory_guidance = update_memory_guidance(state)
        state = await self._update_exhaustion_mode(state, stage="govern_context")
        await self.metrics.emit(
            state,
            "govern_context",
            {
                "plan_items": len(plan.get("actions", [])) if isinstance(plan, dict) else 0,
                "deliberative_steps": len(deliberative.reasoning_chain),
                "planning_success_probability": deliberative.probabilistic_projection.success_probability,
                "planning_uncertainty": deliberative.probabilistic_projection.uncertainty,
                "memory_guidance_present": bool(memory_guidance),
                "exhaustion_mode": state.get("exhaustion_mode", ExhaustionMode.NONE).value,
                "exhaustion_probability": float(state.get("exhaustion_probability", 0.0)),
                "context_window_used": state.get("context_window_used", 0),
            },
        )
        self._apply_prp_transition(state, PRPState.EXECUTE)
        await self._flush_prp_metrics(state)
        self.time_travel.log_stage(
            state,
            stage="govern_context",
            payload={
                "plan": plan,
                "prefetch_queue": len(state.get("prefetch_queue", [])),
                "deliberative_synopsis": deliberative.synopsis,
                "deliberative_steps": len(deliberative.reasoning_chain),
                "memory_guidance": memory_guidance,
            },
        )
        self._record_stage_observability(state, "govern_context")
        
        # IMPORTANT: Remove messages to prevent duplication via add_messages reducer
        state.pop("messages", None)
        return state

    async def handle_tool_response(
        self, state: QuadraCodeState, tool_response: Any | None = None
    ) -> Dict[str, Any]:
        """
        Processes a tool response and updates the context.

        This method is called when a tool has been executed. It scores the 
        relevance of the tool's output, decides on an appropriate operation 
        (e.g., retain, summarize, discard), and applies that operation to 
        incorporate the tool's output into the context.

        Args:
            state: The current state of the system.
            tool_response: The output from the tool.

        Returns:
            The updated state.
        """
        state = state.copy()
        state = self._ensure_state_defaults(state)

        incoming = tool_response
        if incoming is None:
            incoming = self._extract_tool_messages(state)

        # Remove legacy messages to prevent duplication via add_messages reducer
        state.pop("messages", None)

        if not incoming:
            return state

        payloads = incoming if isinstance(incoming, list) else [incoming]
        new_messages: List[ToolMessage] = []
        operations_summary: Dict[str, int] = {}
        last_payload: Any = None

        for payload in payloads:
            last_payload = payload
            if isinstance(payload, ToolMessage):
                new_messages.append(payload)
                state, _ = process_autonomous_tool_response(state, payload)
                state, _ = process_manage_refinement_ledger_tool_response(state, payload)
                operation = await self._decide_operation(1.0, state)
                state = await self._apply_operation(state, payload, operation)
                self._capture_testing_outputs(state, payload)
                self._maybe_issue_skepticism_challenge(state, payload)
            else:
                operation = await self._decide_operation(1.0, state)
                state = await self._apply_operation(state, payload, operation)
            operations_summary[operation.value] = operations_summary.get(operation.value, 0) + 1

        if new_messages:
            state["messages"] = new_messages

        current_prp = state.get("prp_state", PRPState.HYPOTHESIZE)
        if not isinstance(current_prp, PRPState):
            try:
                current_prp = PRPState(str(current_prp))
            except ValueError:
                current_prp = PRPState.HYPOTHESIZE
        prp_transition_map = {
            PRPState.HYPOTHESIZE: PRPState.EXECUTE,
            PRPState.EXECUTE: PRPState.TEST,
            PRPState.TEST: PRPState.CONCLUDE,
            PRPState.CONCLUDE: PRPState.PROPOSE,
        }
        next_prp = prp_transition_map.get(current_prp)
        if next_prp is not None:
            self._apply_prp_transition(state, next_prp)
        await self._flush_prp_metrics(state)
        await self.metrics.emit(
            state,
            "tool_response",
            {
                "count": len(payloads),
                "operations": operations_summary,
                "context_window_used": state.get("context_window_used", 0),
            },
        )
        state = await self._update_exhaustion_mode(
            state,
            stage="handle_tool_response",
            tool_response=last_payload,
        )
        return state

    async def _decide_operation(
        self, relevance: float, state: QuadraCodeState
    ) -> ContextOperation:
        """
        Decides which context operation to apply to a tool response based on its 
        relevance score.
        """
        if relevance < 0.2:
            return ContextOperation.DISCARD
        if relevance < 0.5:
            return ContextOperation.SUMMARIZE
        return ContextOperation.RETAIN

    async def _apply_operation(
        self,
        state: QuadraCodeState,
        tool_response: Any,
        operation: ContextOperation,
    ) -> QuadraCodeState:
        """
        Applies a context operation to a tool response, creating a new context 
        segment.
        """
        segment = self._build_segment_from_tool(tool_response, state)

        if operation is ContextOperation.DISCARD:
            return state

        needs_reduction = (
            operation in {ContextOperation.SUMMARIZE, ContextOperation.COMPRESS}
            or len(segment["content"]) > self.max_tool_payload_chars
        )

        if needs_reduction:
            prior_content = segment.get("content", "")
            prior_tokens = int(segment.get("token_count", len(prior_content.split())) or 0)
            reduction_reason = (
                f"operation::{operation.value}"
                if operation in {ContextOperation.SUMMARIZE, ContextOperation.COMPRESS}
                else "tool_payload_limit"
            )
            reduced = await self.reducer.reduce(
                segment["content"], focus=segment.get("type")
            )
            segment["content"] = reduced.content
            segment["token_count"] = reduced.token_count
            await log_context_compression(
                state,
                action="tool_payload_reduction",
                stage="context_engine.handle_tool_response",
                reason=reduction_reason,
                segment_id=segment.get("id"),
                segment_type=segment.get("type"),
                before_tokens=prior_tokens,
                after_tokens=reduced.token_count,
                before_content=prior_content,
                after_content=reduced.content,
                metadata={
                    "operation": operation.value,
                    "tool_name": getattr(tool_response, "name", None)
                    if isinstance(tool_response, ToolMessage)
                    else None,
                    "tool_call_id": getattr(tool_response, "tool_call_id", None)
                    if isinstance(tool_response, ToolMessage)
                    else None,
                },
            )
            segment["restorable_reference"] = segment.get("restorable_reference") or segment["id"]

        state["context_segments"].append(segment)
        state["working_memory"][segment["id"]] = segment
        state = self._recompute_context_usage(state)
        return state

    def _build_segment_from_tool(
        self, tool_response: Any, state: QuadraCodeState
    ) -> ContextSegment:
        """Builds a `ContextSegment` from a tool response."""
        segment_id = f"tool-{len(state['context_segments']) + 1}"
        restorable_reference: str | None = None
        segment_type = "tool_output"

        if isinstance(tool_response, ToolMessage):
            content = self._render_tool_message(tool_response)
            tool_name = (tool_response.name or "").strip()
            if tool_name:
                segment_type = f"tool_output:{tool_name}"
            if tool_response.tool_call_id:
                restorable_reference = tool_response.tool_call_id
        else:
            content = str(tool_response)

        normalized_content = content.strip() or "Tool returned no textual output."

        return {
            "id": segment_id,
            "content": normalized_content,
            "type": segment_type,
            "priority": 5,
            "token_count": len(normalized_content.split()),
            "timestamp": _utc_now().isoformat(),
            "decay_rate": 0.1,
            "compression_eligible": True,
            "restorable_reference": restorable_reference,
        }

    async def _handle_tool_messages(
        self, state: QuadraCodeState, tool_messages: List[ToolMessage]
    ) -> QuadraCodeState:
        """Iteratively processes a list of tool messages."""
        current = state
        for message in tool_messages:
            current = await self.handle_tool_response(current, message)
        return current

    def _capture_testing_outputs(self, state: QuadraCodeState, message: ToolMessage) -> None:
        tool_name = (message.name or "").strip()
        if tool_name not in {"run_full_test_suite", "request_final_review", "generate_property_tests"}:
            return
        payload = self._parse_tool_json(message)
        if not isinstance(payload, dict):
            return
        if tool_name == "request_final_review":
            tests_payload = payload.get("tests")
            if isinstance(tests_payload, dict):
                record_test_suite_result(state, tests_payload)
            self._evaluate_completion_request_for_false_stop(
                state,
                tests_payload if isinstance(tests_payload, dict) else None,
                message,
            )
            return
        if tool_name == "generate_property_tests":
            record_property_test_result(state, payload)
            return
        record_test_suite_result(state, payload)

    def _evaluate_completion_request_for_false_stop(
        self,
        state: QuadraCodeState,
        tests_payload: Dict[str, Any] | None,
        message: ToolMessage,
    ) -> None:
        reason: Optional[str] = None
        evidence: Dict[str, Any] = {}
        tests_status = None
        if tests_payload is None:
            reason = "missing_tests"
        else:
            tests_status = str(tests_payload.get("overall_status") or "").lower()
            if tests_status not in {"passed", "pass", "success"}:
                reason = "tests_not_passed"
                evidence["tests_status"] = tests_status or "unknown"

        if reason is None:
            property_result = state.get("last_property_test_result")
            if isinstance(property_result, dict):
                property_status = str(property_result.get("status") or "").lower()
                if property_status and property_status not in {"passed", "pass", "success"}:
                    reason = "property_tests_unresolved"
                    evidence["property_status"] = property_status

        if reason is None:
            requirements = state.get("human_clone_requirements")
            if isinstance(requirements, list):
                outstanding = [str(item) for item in requirements if str(item).strip()]
                if outstanding:
                    reason = "artifact_requirements_pending"
                    evidence["requirements"] = outstanding

        if reason is None:
            last_suite = state.get("last_test_suite_result")
            if isinstance(last_suite, dict):
                last_status = str(last_suite.get("overall_status") or "").lower()
                if last_status and last_status not in {"passed", "pass", "success"}:
                    reason = "prior_tests_failed"
                    evidence["last_suite_status"] = last_status

        if not reason:
            return

        evidence_payload = {
            **evidence,
            "tests_status": tests_status or ("unknown" if tests_payload else "missing"),
            "tool_call_id": message.tool_call_id,
            "tool_name": message.name,
        }
        self._handle_false_stop(
            state,
            reason=reason,
            stage="request_final_review",
            evidence=evidence_payload,
        )

    def _extract_tool_messages(
        self, state: QuadraCodeState
    ) -> List[ToolMessage]:
        messages = state.get("messages") or []
        trailing: List[ToolMessage] = []
        for message in reversed(messages):
            if isinstance(message, ToolMessage):
                trailing.append(message)
                continue
            break
        trailing.reverse()
        return trailing

    def _render_tool_message(self, message: ToolMessage) -> str:
        lines: List[str] = []
        tool_name = (message.name or "").strip()
        if tool_name:
            lines.append(f"Tool: {tool_name}")
        if message.tool_call_id:
            lines.append(f"Call ID: {message.tool_call_id}")

        metadata = {
            key: value
            for key, value in (message.additional_kwargs or {}).items()
            if value not in ({}, [], "", None)
        }
        if metadata:
            lines.append(
                f"Metadata: {json.dumps(metadata, ensure_ascii=False, sort_keys=True)}"
            )

        body = self._coerce_tool_content(message.content)
        if not body and "output" in metadata and isinstance(metadata["output"], str):
            body = metadata["output"].strip()

        body = body.strip()
        if body:
            if lines:
                lines.append("")
            lines.append(body)

        return "\n".join(lines).strip()

    def _coerce_tool_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            fragments = [self._coerce_tool_content(item) for item in content]
            return "\n".join(fragment for fragment in fragments if fragment)
        if isinstance(content, dict):
            text_candidates = [
                content.get(key)
                for key in ("text", "content", "message", "output")
                if isinstance(content.get(key), str)
            ]
            normalized = [candidate.strip() for candidate in text_candidates if candidate]
            residual = {
                key: value
                for key, value in content.items()
                if key not in {"text", "content", "message", "output"}
                and value not in ({}, [], "", None)
            }
            body_parts = normalized
            if residual:
                body_parts.append(json.dumps(residual, ensure_ascii=False, sort_keys=True))
            return "\n".join(body_parts)
        return str(content)

    def _parse_tool_json(self, message: ToolMessage) -> Any:
        candidates: List[str] = []
        raw = message.content
        if isinstance(raw, str):
            candidates.append(raw)
        coerced = self._coerce_tool_content(raw)
        if coerced:
            candidates.append(coerced)
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except Exception:
                continue
        return None

    async def _generate_governor_plan(self, state: QuadraCodeState) -> Dict[str, Any]:
        override = state.pop("governor_plan_override", None)
        if override:
            return override

        if self.governor_model in {"heuristic", ""}:
            return self._heuristic_governor_plan(state)

        llm = await self._ensure_governor_llm()
        snapshot = self._plan_segments_snapshot(state)
        summary = self._plan_context_summary(state, snapshot)

        # Use configurable prompts
        prompts = self.config.prompt_templates
        system_prompt = prompts.governor_system_prompt
        instructions = prompts.governor_instructions
        
        payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        
        # Format the input using the template
        formatted_input = prompts.get_prompt(
            "governor_input_format",
            instructions=instructions,
            payload=payload
        )
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=formatted_input),
        ]
        response = await asyncio.to_thread(llm.invoke, messages)
        return self._parse_plan_response(response.content)

    async def _apply_governor_plan(
        self, state: QuadraCodeState, plan: Dict[str, Any]
    ) -> QuadraCodeState:
        actions = plan.get("actions") or []
        action_counts: Dict[str, int] = {}
        segments_by_id = {segment["id"]: segment for segment in state.get("context_segments", [])}
        modified: Dict[str, ContextSegment] = {}
        removed: set[str] = set()

        for action in actions:
            if not isinstance(action, dict):
                continue
            segment_id = str(action.get("segment_id") or "").strip()
            decision = str(action.get("decision") or "").lower().strip()
            if not segment_id or decision not in {
                "retain",
                "compress",
                "summarize",
                "isolate",
                "externalize",
                "discard",
            }:
                continue

            segment = segments_by_id.get(segment_id)
            if not segment:
                continue

            action_counts[decision] = action_counts.get(decision, 0) + 1
            segment_copy = dict(segment)

            if decision == "discard":
                removed.add(segment_id)
                continue

            if decision == "externalize":
                pointer, reference = await asyncio.to_thread(
                    self.curator._externalize_segment, segment_copy
                )
                modified[segment_id] = pointer
                state["external_memory_index"][reference["id"]] = reference["path"]
                state.setdefault("recent_externalizations", []).append(
                    {
                        "id": reference.get("id"),
                        "path": reference.get("path"),
                        "source_segment": segment_id,
                        "source_type": segment.get("type"),
                        "source_tokens": segment.get("token_count", 0),
                        "timestamp": pointer.get("timestamp"),
                    }
                )
                continue

            if decision == "summarize":
                summarized = self.curator._summarize_segment(segment_copy)
                summarized["restorable_reference"] = segment_copy.get("restorable_reference") or segment_id
                summarized["compression_eligible"] = False
                summarized["token_count"] = max(1, len(summarized["content"].split()))
                summarized["type"] = f"summary:{segment_copy.get('type', 'generic')}"
                modified[segment_id] = summarized
                continue

            if decision == "compress":
                compressed = self.curator._compress_segment(segment_copy)
                compressed["token_count"] = max(1, len(compressed["content"].split()))
                modified[segment_id] = compressed
                continue

            if decision == "isolate":
                segment_copy["priority"] = max(1, segment_copy.get("priority", 5) - 1)
                modified[segment_id] = segment_copy
                continue

            if decision == "retain":
                priority = action.get("priority")
                if priority is not None:
                    try:
                        segment_copy["priority"] = max(1, int(priority))
                    except (TypeError, ValueError):
                        pass
                modified[segment_id] = segment_copy

        updated_segments: List[ContextSegment] = []
        for segment in state.get("context_segments", []):
            segment_id = segment["id"]
            if segment_id in removed:
                continue
            updated_segments.append(modified.get(segment_id, segment))

        outline = plan.get("prompt_outline") or {}
        ordered_segments = outline.get("ordered_segments") or []
        if ordered_segments:
            order_index = {segment_id: idx for idx, segment_id in enumerate(ordered_segments)}
            updated_segments.sort(
                key=lambda seg: (
                    order_index.get(seg["id"], len(order_index)),
                    seg.get("timestamp") or "",
                )
            )

        state["context_segments"] = updated_segments
        state["working_memory"] = {segment["id"]: segment for segment in updated_segments}
        state["governor_plan"] = plan
        state["governor_prompt_outline"] = outline
        state = self._recompute_context_usage(state)

        await self.metrics.emit(
            state,
            "governor_plan",
            {
                "action_counts": action_counts,
                "ordered_segments": ordered_segments[: self.config.governor_max_segments],
                "focus": outline.get("focus"),
                "context_window_used": state.get("context_window_used", 0),
            },
        )
        await self._emit_externalization_metrics(state)
        await self._emit_externalization_metrics(state)
        return state

    def _store_deliberative_plan(
        self,
        state: QuadraCodeState,
        artifacts: DeliberativePlanArtifacts,
    ) -> None:
        plan_payload = artifacts.to_dict()
        state["deliberative_plan"] = plan_payload
        state["deliberative_intermediate_states"] = [
            dict(item) for item in artifacts.intermediate_states
        ]
        state["deliberative_synopsis"] = artifacts.synopsis
        projection = artifacts.probabilistic_projection
        state["planning_success_probability"] = projection.success_probability
        state["planning_uncertainty"] = projection.uncertainty
        state["counterfactual_register"] = [
            scenario.to_dict() for scenario in artifacts.counterfactuals
        ]
        state["causal_graph_snapshot"] = artifacts.causal_graph.to_dict()

    async def _ensure_governor_llm(self):
        if self.governor_model in {"heuristic", ""}:
            raise RuntimeError("Heuristic governor does not use an LLM")
        if self._governor_llm is not None:
            return self._governor_llm

        async with self._governor_lock:
            if self._governor_llm is None:
                self._governor_llm = init_chat_model(self.governor_model)
        return self._governor_llm

    def _heuristic_governor_plan(self, state: QuadraCodeState) -> Dict[str, Any]:
        segments = list(state.get("context_segments", []))
        max_segments = max(1, self.config.governor_max_segments)
        sorted_segments = sorted(
            segments,
            key=lambda seg: (-seg.get("priority", 5), seg.get("timestamp", "")),
        )

        actions: List[Dict[str, Any]] = []
        for idx, segment in enumerate(sorted_segments):
            segment_id = segment.get("id")
            if not segment_id:
                continue
            decision = "retain"
            if idx >= max_segments and segment.get("compression_eligible", True):
                decision = "summarize"
            elif segment.get("token_count", 0) > self.config.reducer_chunk_tokens:
                decision = "compress"
            actions.append({"segment_id": segment_id, "decision": decision})

        # Use the configurable governor message for the driver
        prompts = self.config.prompt_templates
        outline = {
            "system": prompts.governor_driver_message,
            "focus": state.get("pending_context", []) or state.get("context_playbook", {}).get("last_reflection", {}).get("focus_metric"),
            "ordered_segments": [segment.get("id") for segment in sorted_segments[:max_segments] if segment.get("id")],
        }

        return {
            "actions": actions,
            "prompt_outline": outline,
        }

    def _plan_segments_snapshot(self, state: QuadraCodeState) -> List[Dict[str, Any]]:
        segments = state.get("context_segments", [])
        limited = segments[-self.config.governor_max_segments :]
        snapshot: List[Dict[str, Any]] = []
        for segment in limited:
            snapshot.append(
                {
                    "id": segment.get("id"),
                    "type": segment.get("type"),
                    "priority": segment.get("priority"),
                    "tokens": segment.get("token_count"),
                    "timestamp": segment.get("timestamp"),
                    "restorable_reference": segment.get("restorable_reference"),
                    "compression_eligible": segment.get("compression_eligible", True),
                }
            )
        return snapshot

    def _plan_context_summary(
        self, state: QuadraCodeState, segments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return {
            "segments": segments,
            "quality": {
                "score": state.get("context_quality_score"),
                "components": state.get("context_quality_components", {}),
            },
            "context": {
                "used_tokens": state.get("context_window_used", 0),
                "max_tokens": state.get("context_window_max", self.config.context_window_max),
                "target_tokens": self.config.target_context_size,
                "pending": state.get("pending_context", []),
            },
            "playbook": state.get("context_playbook", {}),
            "prefetch_queue": state.get("prefetch_queue", []),
        }

    def _parse_plan_response(self, content: Any) -> Dict[str, Any]:
        text = str(content or "").strip()
        if "```" in text:
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Context governor returned invalid JSON plan") from exc

    async def _reflect_on_decision(self, state: QuadraCodeState) -> ReflectionResult:
        timestamp = _utc_now().isoformat()
        quality_score = state.get("context_quality_score", 0.0)
        components = state.get("context_quality_components", {})

        issues: List[str] = []
        recommendations: List[str] = []
        focus_metric: str | None = None

        if components:
            sorted_metrics = sorted(components.items(), key=lambda item: item[1])
            focus_metric, focus_value = sorted_metrics[0]

            for metric, value in sorted_metrics:
                if value >= self.config.quality_threshold:
                    continue
                issues.append(f"{metric} below threshold ({value:.2f})")
                recommendations.extend(self._recommendations_for_metric(metric, state))

            # If all metrics healthy, clear focus metric
            if all(value >= self.config.quality_threshold for _, value in sorted_metrics):
                focus_metric = None

        if state.get("pending_context"):
            recommendations.append(
                f"load pending context types: {', '.join(state['pending_context'])}"
            )

        summary_bits = [
            f"Context quality {quality_score:.2f}",
        ]
        if issues:
            summary_bits.append(f"Issues: {len(issues)} detected")
        if not issues:
            summary_bits.append("All metrics within healthy range")

        summary = " | ".join(summary_bits)

        return ReflectionResult(
            timestamp=timestamp,
            quality_score=quality_score,
            summary=summary,
            issues=issues,
            recommendations=self._deduplicate(recommendations),
            focus_metric=focus_metric,
        )

    async def _evolve_playbook(
        self, state: QuadraCodeState, reflection: ReflectionResult
    ) -> QuadraCodeState:
        playbook = state["context_playbook"]

        iterations = playbook.get("iterations", 0) + 1
        playbook["iterations"] = iterations
        playbook["last_updated"] = reflection.timestamp
        playbook["last_reflection"] = {
            "summary": reflection.summary,
            "quality_score": reflection.quality_score,
            "issues": reflection.issues,
            "recommendations": reflection.recommendations,
            "focus_metric": reflection.focus_metric,
        }

        strategies: List[Dict[str, Any]] = playbook.setdefault("strategies", [])
        if reflection.recommendations:
            strategies.append(
                {
                    "iteration": iterations,
                    "focus": reflection.focus_metric,
                    "actions": reflection.recommendations,
                    "quality": reflection.quality_score,
                }
            )
            if len(strategies) > self.config.curation_rules_max:
                del strategies[0]

        focus_counts = playbook.setdefault("focus_counts", {})
        if reflection.focus_metric:
            focus_counts[reflection.focus_metric] = focus_counts.get(reflection.focus_metric, 0) + 1

        self._update_curation_rules(state, reflection)
        return state

    async def _enforce_limits(
        self, state: QuadraCodeState
    ) -> QuadraCodeState:
        state = self._recompute_context_usage(state)
        max_tokens = state.get("context_window_max") or self.config.context_window_max
        state["context_window_max"] = max_tokens

        used = state.get("context_window_used", 0)
        state["context_window_used"] = min(used, max_tokens)

        if max_tokens:
            state["compression_ratio"] = (
                state["context_window_used"] / max_tokens
                if max_tokens
                else 0.0
            )

        return state

    def _should_checkpoint(self, state: QuadraCodeState) -> bool:
        frequency = self.config.checkpoint_frequency
        if not frequency:
            return False

        iteration = state.get("context_playbook", {}).get("iterations", 0)
        return iteration > 0 and iteration % frequency == 0

    def _ensure_state_defaults(
        self, state: QuadraCodeState
    ) -> QuadraCodeState:
        defaults = make_initial_context_engine_state(
            context_window_max=self.config.context_window_max
        )
        for key, default_value in defaults.items():
            if key not in state or state[key] is None:
                state[key] = default_value  # type: ignore[index]

        if not state["context_window_max"]:
            state["context_window_max"] = self.config.context_window_max

        state = self._normalize_refinement_ledger(state)
        state["refinement_memory_block"] = self._render_refinement_memory(state)

        return state

    @staticmethod
    def _truncate_content(content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        return f"{content[:limit]}…"

    def _recompute_context_usage(
        self, state: QuadraCodeState
    ) -> QuadraCodeState:
        # Count tokens from context segments
        segment_tokens = sum(
            max(segment.get("token_count", 0), 0)
            for segment in state.get("context_segments", [])
        )
        
        # Count tokens from messages
        message_tokens = self._count_message_tokens(state)
        
        # Total context window usage includes static prompt, segments and messages
        total = self.system_prompt_tokens + segment_tokens + message_tokens
        state["context_window_used"] = int(total)
        
        # Store breakdown for observability
        state.setdefault("_context_breakdown", {})
        state["_context_breakdown"]["segment_tokens"] = int(segment_tokens)
        state["_context_breakdown"]["message_tokens"] = int(message_tokens)
        
        return state

    async def _manage_conversation_history(self, state: QuadraCodeState, available_dynamic_space: int) -> Dict[str, Any]:
        """
        Manages conversation history by summarizing and trimming messages when they exceed their budget.
        """
        # Calculate message budget based on available dynamic space
        message_budget = int(available_dynamic_space * self.config.message_budget_ratio)
        
        breakdown = state.get("_context_breakdown", {})
        message_tokens = breakdown.get("message_tokens", 0)

        if message_tokens <= message_budget:
            return {}
            
        messages: list[BaseMessage] = state.get("messages", [])
        if not messages:
            return {}

        # Always keep at least the most recent message (current user input)
        # to ensure the LLM has something to respond to
        if len(messages) <= 1:
            # Can't compress if we only have one message
            return {}
        
        # Summarize just enough to get back under budget
        tokens_to_remove = message_tokens - message_budget
        
        # Find oldest messages to summarize, but keep at least the last message
        summarization_candidates: list[BaseMessage] = []
        removed_tokens = 0
        # Leave at least the last message untouched
        for msg in messages[:-1]:
            msg_tokens = self._estimate_tokens(str(msg.content))
            if removed_tokens < tokens_to_remove:
                summarization_candidates.append(msg)
                removed_tokens += msg_tokens
            else:
                break
        
        if not summarization_candidates:
            return {}
            
        # Generate summary
        summary_text = get_buffer_string(summarization_candidates)
        existing_summary = state.get("conversation_summary", "")
        
        prompt = self.config.prompt_templates.get_prompt(
            "conversation_summarization_prompt",
            existing_summary=existing_summary or "None",
            new_lines=summary_text
        )
        
        reduction = await self.reducer.reduce(prompt, focus="conversation_summary")
        new_summary = reduction.content
        
        # Construct updates to be returned
        updates: Dict[str, Any] = {
            "conversation_summary": new_summary,
            "messages": [RemoveMessage(id=msg.id) for msg in summarization_candidates if msg.id]
        }
        
        # Create a new context segment for the summary
        segment = {
            "id": "conversation-summary",
            "content": new_summary,
            "type": "conversation_summary",
            "priority": 10,  # High priority
            "token_count": reduction.token_count,
            "timestamp": _utc_now().isoformat(),
            "compression_eligible": False,
        }
        
        # Replace or add the summary segment
        existing_segments = [s for s in state.get("context_segments", []) if s["id"] != "conversation-summary"]
        existing_segments.insert(0, segment)
        state["context_segments"] = existing_segments
        
        # Log the summarization event
        await log_context_compression(
            state,
            action="summarize_history",
            stage="context_engine.manage_history",
            reason="message_budget_exceeded",
            segment_id="conversation_history",
            segment_type="messages",
            before_tokens=message_tokens,
            after_tokens=message_tokens - int(removed_tokens), # Approximation
            before_content=summary_text[:200] + "...",
            after_content=new_summary,
            metadata={
                "removed_messages": len(summarization_candidates),
                "budget_ratio": self.config.message_budget_ratio
            }
        )
        
        state["last_compression_event"] = {
             "timestamp": _utc_now().isoformat(),
             "action": "summarize_history",
             "before_tokens": message_tokens,
             "tokens_saved": int(removed_tokens),
             "summary": new_summary[:100] + "..."
        }
        
        return updates

    def _count_message_tokens(self, state: QuadraCodeState) -> int:
        """
        Counts tokens in the conversation messages.
        
        Uses usage_metadata from the most recent AI message if available,
        otherwise estimates based on message content length.
        
        Args:
            state: The current state containing messages
            
        Returns:
            Estimated total token count for all messages
        """
        messages = state.get("messages", [])
        if not messages:
            return 0
        
        # Try to get actual token count from the most recent AI message's usage_metadata
        for msg in reversed(messages):
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                # usage_metadata contains input_tokens and output_tokens
                # input_tokens includes all previous messages + context
                # So we return just the input_tokens as it represents the full conversation window
                return int(msg.usage_metadata.get("input_tokens", 0))
        
        # Fallback: rough estimate based on character count
        total_chars = 0
        for msg in messages:
            content = ""
            if hasattr(msg, "content"):
                if isinstance(msg.content, str):
                    content = msg.content
                elif isinstance(msg.content, list):
                    # Handle content blocks (text, tool calls, etc.)
                    for block in msg.content:
                        if isinstance(block, dict):
                            content += str(block.get("text", ""))
                        elif isinstance(block, str):
                            content += block
                        else:
                            content += str(block)
            total_chars += len(content)
        
        # Estimate tokens as 1 token per 4 characters
        return int(total_chars / 4)

    def _normalize_refinement_ledger(self, state: QuadraCodeState) -> QuadraCodeState:
        ledger_entries: List[RefinementLedgerEntry] = []
        raw_entries = state.get("refinement_ledger", [])
        for entry in raw_entries:
            resolved = self._coerce_ledger_entry(entry)
            if resolved:
                ledger_entries.append(resolved)
        state["refinement_ledger"] = ledger_entries
        return state

    def _coerce_ledger_entry(
        self, entry: Any
    ) -> RefinementLedgerEntry | None:
        if isinstance(entry, RefinementLedgerEntry):
            return entry
        if not isinstance(entry, dict):
            return None
        payload = dict(entry)
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str):
            try:
                payload["timestamp"] = datetime.fromisoformat(timestamp)
            except ValueError:
                payload["timestamp"] = _utc_now()
        elif isinstance(timestamp, datetime):
            payload["timestamp"] = timestamp
        else:
            payload["timestamp"] = _utc_now()
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
            payload["causal_links"] = [
                dict(link)
                for link in causal_links
                if isinstance(link, dict)
            ]
        else:
            payload["causal_links"] = []
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            payload["metadata"] = dict(metadata)
        else:
            payload["metadata"] = {}
        try:
            return RefinementLedgerEntry(**payload)
        except Exception:
            return None

    def _render_refinement_memory(self, state: QuadraCodeState) -> str:
        ledger_entries = state.get("refinement_ledger", [])
        if not ledger_entries:
            return ""
        limit = getattr(self.config, "refinement_memory_limit", 5) or 5
        recent_entries = sorted(ledger_entries, key=lambda item: item.timestamp)[-limit:]
        formatted_lines = [
            f"- {entry.formatted_summary()}" for entry in recent_entries
        ]
        return "Refinement Ledger (latest cycles):\n" + "\n".join(formatted_lines)

    async def _update_exhaustion_mode(
        self,
        state: QuadraCodeState,
        *,
        stage: str,
        tool_response: Any | None = None,
    ) -> QuadraCodeState:
        previous_mode = self._coerce_exhaustion_mode(state.get("exhaustion_mode"))
        mode, probability = self._classify_exhaustion(
            state,
            stage=stage,
            tool_response=tool_response,
            previous_mode=previous_mode,
        )
        state["exhaustion_mode"] = mode
        state["exhaustion_probability"] = float(probability)

        if mode == previous_mode:
            return state

        if mode is ExhaustionMode.LLM_STOP:
            self._handle_false_stop(
                state,
                reason="llm_stop",
                stage=stage,
                evidence={
                    "probability": float(probability),
                    "previous_mode": previous_mode.value,
                },
            )

        if mode is not ExhaustionMode.NONE:
            await self._handle_workspace_integrity_event(
                state,
                stage=stage,
                mode=mode,
            )

        action = None
        if mode is not ExhaustionMode.NONE:
            action = await self._apply_exhaustion_strategy(
                state,
                mode,
                stage=stage,
                previous_mode=previous_mode,
                tool_response=tool_response,
            )
        else:
            action = "cleared"
            self._record_recovery_action(state, stage, mode, action)

        await self.metrics.emit(
            state,
            "exhaustion_update",
            {
                "stage": stage,
                "mode": mode.value,
                "previous_mode": previous_mode.value,
                "probability": float(probability),
                "context_ratio": self._context_ratio(state),
                "ledger_size": len(state.get("refinement_ledger", [])),
                "action": action,
            },
        )
        if mode != previous_mode:
            try:
                self._meta_observer.publish_exhaustion_event(
                    state,
                    stage=stage,
                    previous_mode=previous_mode,
                    mode=mode,
                    probability=float(probability),
                )
            except Exception:  # pragma: no cover - observability is best-effort
                pass
            self.time_travel.log_transition(
                state,
                event="exhaustion_update",
                payload={
                    "stage": stage,
                    "from": previous_mode.value,
                    "to": mode.value,
                    "action": action,
                },
                state_update={
                    "exhaustion_mode": mode.value,
                    "exhaustion_probability": float(probability),
                },
            )
        return state

    def _classify_exhaustion(
        self,
        state: QuadraCodeState,
        *,
        stage: str,
        tool_response: Any | None = None,
        previous_mode: ExhaustionMode,
    ) -> tuple[ExhaustionMode, float]:
        ledger: Sequence[RefinementLedgerEntry] = state.get("refinement_ledger", [])
        probability = self.exhaustion_predictor.predict_probability(ledger)
        candidates: List[tuple[int, ExhaustionMode]] = []

        if stage == "pre_process" and probability >= self.exhaustion_predictor.threshold:
            candidates.append((0, ExhaustionMode.PREDICTED_EXHAUSTION))
        elif (
            previous_mode is ExhaustionMode.PREDICTED_EXHAUSTION
            and probability >= self.exhaustion_predictor.threshold * 0.9
        ):
            candidates.append((0, ExhaustionMode.PREDICTED_EXHAUSTION))

        context_ratio = self._context_ratio(state)
        if context_ratio >= 0.98:
            candidates.append((1, ExhaustionMode.CONTEXT_SATURATION))
        elif context_ratio >= 0.9:
            candidates.append((3, ExhaustionMode.CONTEXT_SATURATION))

        backlog = len(state.get("prefetch_queue", []))
        if backlog > getattr(self.config, "prefetch_queue_limit", 20):
            candidates.append((2, ExhaustionMode.TOOL_BACKPRESSURE))

        if self._ledger_has_test_failure(ledger):
            candidates.append((2 if stage == "handle_tool_response" else 4, ExhaustionMode.TEST_FAILURE))

        recent_exhaustions = [
            entry.exhaustion_trigger
            for entry in ledger[-3:]
            if entry.exhaustion_trigger
        ]
        if any(mode is ExhaustionMode.HYPOTHESIS_EXHAUSTED for mode in recent_exhaustions):
            candidates.append((2, ExhaustionMode.HYPOTHESIS_EXHAUSTED))

        recent_failures = self._recent_failure_count(ledger, window=3)
        if recent_failures >= 3:
            candidates.append((2, ExhaustionMode.RETRY_DEPLETION))
        elif recent_failures >= 2 and stage == "govern_context":
            candidates.append((3, ExhaustionMode.RETRY_DEPLETION))

        working_memory = state.get("working_memory", {})
        if isinstance(working_memory, dict) and working_memory.get("llm_stop"):
            candidates.append((1, ExhaustionMode.LLM_STOP))

        if self._tool_indicates_llm_stop(tool_response):
            candidates.append((1, ExhaustionMode.LLM_STOP))

        if not candidates:
            return ExhaustionMode.NONE, float(probability)

        candidates.sort(key=lambda item: item[0])
        mode = candidates[0][1]
        return mode, float(probability)

    @staticmethod
    def _context_ratio(state: QuadraCodeState) -> float:
        max_tokens = state.get("context_window_max") or 0
        used = state.get("context_window_used", 0)
        if not max_tokens or used <= 0:
            return 0.0
        return float(min(1.0, max(0.0, used / max_tokens)))

    def _recent_failure_count(
        self, ledger: Sequence[RefinementLedgerEntry], *, window: int
    ) -> int:
        if window <= 0:
            return 0
        recent = ledger[-window:]
        return sum(1 for entry in recent if self._status_is_failure(entry.status))

    @staticmethod
    def _status_is_failure(status: Optional[str]) -> bool:
        if not status:
            return False
        lowered = status.lower()
        return any(keyword in lowered for keyword in {"fail", "reject", "error", "timeout"})

    @staticmethod
    def _status_is_success(status: Optional[str]) -> bool:
        if not status:
            return False
        lowered = status.lower()
        return any(keyword in lowered for keyword in {"success", "pass", "complete", "resolved"})

    def _ledger_has_test_failure(
        self, ledger: Sequence[RefinementLedgerEntry]
    ) -> bool:
        if not ledger:
            return False
        last_entry = ledger[-1]
        if last_entry.exhaustion_trigger is ExhaustionMode.TEST_FAILURE:
            return True
        results = last_entry.test_results
        if isinstance(results, dict):
            for key, value in results.items():
                key_lower = str(key).lower()
                if "fail" in key_lower and value:
                    return True
        elif isinstance(results, list):
            return any("fail" in str(item).lower() for item in results)
        elif isinstance(results, str):
            if "fail" in results.lower():
                return True
        return self._status_is_failure(last_entry.status)

    def _tool_indicates_llm_stop(self, tool_response: Any | None) -> bool:
        if not isinstance(tool_response, ToolMessage):
            return False
        content = self._render_tool_message(tool_response).lower()
        return "stop sequence" in content or "max_tokens" in content

    async def _apply_exhaustion_strategy(
        self,
        state: QuadraCodeState,
        mode: ExhaustionMode,
        *,
        stage: str,
        previous_mode: ExhaustionMode,
        tool_response: Any | None = None,
    ) -> Optional[str]:
        action_taken: Optional[str] = None

        if mode is ExhaustionMode.CONTEXT_SATURATION:
            state = await self.curator.optimize(state)
            state = self._recompute_context_usage(state)
            action_taken = "context_compaction"
        elif mode is ExhaustionMode.TOOL_BACKPRESSURE:
            backlog = list(state.get("prefetch_queue", []))
            limit = getattr(self.config, "prefetch_queue_limit", 20)
            if len(backlog) > limit:
                state["prefetch_queue"] = backlog[:limit]
            action_taken = "prefetch_throttled"
        elif mode in {ExhaustionMode.RETRY_DEPLETION, ExhaustionMode.TEST_FAILURE, ExhaustionMode.HYPOTHESIS_EXHAUSTED}:
            self._apply_prp_transition(state, PRPState.HYPOTHESIZE)
            action_taken = "hypothesis_refinement"
        elif mode is ExhaustionMode.LLM_STOP:
            working_memory = state.setdefault("working_memory", {})
            if isinstance(working_memory, dict):
                working_memory["llm_resume_hint"] = True
            action_taken = "llm_resume_hint"
        elif mode is ExhaustionMode.PREDICTED_EXHAUSTION:
            self._apply_prp_transition(state, PRPState.HYPOTHESIZE)
            action_taken = "preemptive_refinement"

        if action_taken:
            self._record_recovery_action(state, stage, mode, action_taken)
            await self.metrics.emit(
                state,
                "exhaustion_recovery",
                {
                    "stage": stage,
                    "mode": mode.value,
                    "previous_mode": previous_mode.value,
                    "action": action_taken,
                    "context_ratio": self._context_ratio(state),
                },
            )

        return action_taken

    def _record_recovery_action(
        self, state: QuadraCodeState, stage: str, mode: ExhaustionMode, action: str
    ) -> None:
        log = state.setdefault("exhaustion_recovery_log", [])
        if isinstance(log, list):
            log.append(
                {
                    "timestamp": _utc_now().isoformat(),
                    "stage": stage,
                    "mode": mode.value,
                    "action": action,
                }
            )
            if len(log) > 50:
                del log[0]

    async def _handle_workspace_integrity_event(
        self,
        state: QuadraCodeState,
        *,
        stage: str,
        mode: ExhaustionMode,
    ) -> None:
        reason = f"exhaustion::{mode.value}"
        validation = await asyncio.to_thread(
            validate_workspace_integrity,
            state,
            reason=reason,
            auto_restore=True,
        )
        metadata = {
            "stage": stage,
            "exhaustion_probability": float(state.get("exhaustion_probability", 0.0)),
        }
        if validation is not None:
            if validation.restored:
                metadata["validation_status"] = "restored"
            elif validation.valid:
                metadata["validation_status"] = "clean"
            else:
                metadata["validation_status"] = "drift_detected"
        await asyncio.to_thread(
            capture_workspace_snapshot,
            state,
            reason=reason,
            stage=stage,
            exhaustion_mode=mode,
            metadata=metadata,
        )

    def _record_stage_observability(self, state: QuadraCodeState, stage: str) -> None:
        try:
            self._meta_observer.track_stage_tokens(state, stage=stage)
        except Exception:  # pragma: no cover - observability is best-effort
            pass
        # We deliberately do NOT await here as this method is called from both sync and async contexts
        # and snapshot logging is best-effort.
        # However, since log_snapshot is now async, we must either await it or schedule it.
        # Given the strict blocking checks, we should await it if possible, but this method is not async.
        # For now, we'll skip snapshot logging in this specific helper to avoid complexity,
        # as the critical metrics are already emitted via self.metrics.emit().
        pass

    async def _enforce_hotpath_residency(self, state: QuadraCodeState) -> None:
        if not self.registry_url:
            return
        agents = await self._fetch_hotpath_agents()
        if not isinstance(agents, list):
            return
        unhealthy = [agent for agent in agents if str(agent.get("status", "")).lower() != "healthy"]
        if not unhealthy:
            state["_hotpath_violation_agents"] = []
            return
        violation_ids = sorted(
            {
                str(agent.get("agent_id"))
                for agent in unhealthy
                if agent.get("agent_id")
            }
        )
        previous_ids = sorted(
            {
                str(agent)
                for agent in state.get("_hotpath_violation_agents", [])
                if agent
            }
        )
        if violation_ids == previous_ids:
            return
        state["_hotpath_violation_agents"] = violation_ids
        payload = {
            "agents": [
                {
                    "agent_id": agent.get("agent_id"),
                    "status": agent.get("status"),
                    "last_heartbeat": agent.get("last_heartbeat"),
                }
                for agent in unhealthy
            ],
            "registry_url": self.registry_url,
        }
        telemetry = state.setdefault("prp_telemetry", [])
        if isinstance(telemetry, list):
            telemetry.append({"event": "hotpath_violation", "payload": payload})
        invariants = state.setdefault("invariants", {})
        if isinstance(invariants, dict):
            log = invariants.setdefault("violation_log", [])
            if isinstance(log, list):
                log.append(
                    {
                        "timestamp": _utc_now().isoformat(),
                        "invariant": "hotpath_residency",
                        "details": payload,
                    }
                )
        try:
            self._meta_observer.publish_autonomous_event("hotpath_violation", payload)
        except Exception:  # pragma: no cover - best-effort
            LOGGER.debug("Failed to publish hotpath violation event", exc_info=True)
        self.time_travel.log_transition(
            state,
            event="hotpath_violation",
            payload=payload,
        )

    async def _fetch_hotpath_agents(self) -> List[Dict[str, Any]]:
        if not self.registry_url:
            return []
        url = f"{self.registry_url}/agents/hotpath"
        try:
            async with httpx.AsyncClient(timeout=self._hotpath_probe_timeout) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    agents = data.get("agents")
                    if isinstance(agents, list):
                        return agents
                if isinstance(data, list):
                    return data
        except Exception as exc:  # pragma: no cover - best-effort
            LOGGER.debug("Hotpath registry probe failed: %s", exc)
        return []

    @staticmethod
    def _coerce_exhaustion_mode(value: Any) -> ExhaustionMode:
        if isinstance(value, ExhaustionMode):
            return value
        if isinstance(value, str):
            try:
                return ExhaustionMode(value)
            except ValueError:
                return ExhaustionMode.NONE
        return ExhaustionMode.NONE

    def _apply_prp_transition(
        self,
        state: QuadraCodeState,
        target_state: PRPState,
        *,
        human_clone_triggered: bool = False,
        strict: bool = False,
    ) -> None:
        apply_prp_transition(
            state,
            target_state,
            exhaustion_mode=state.get("exhaustion_mode"),
            human_clone_triggered=human_clone_triggered,
            strict=strict,
        )

    def _handle_false_stop(
        self,
        state: QuadraCodeState,
        *,
        reason: str,
        stage: str,
        evidence: Dict[str, Any] | None = None,
    ) -> None:
        payload = flag_false_stop_event(
            state,
            reason=reason,
            stage=stage,
            evidence=evidence or {},
        )
        self._record_skepticism_gate(
            state,
            source="false_stop",
            reason=reason,
            evidence=payload,
        )
        self._apply_prp_transition(state, PRPState.HYPOTHESIZE)

    def _record_skepticism_gate(
        self,
        state: QuadraCodeState,
        *,
        source: str,
        reason: str,
        evidence: Dict[str, Any] | None = None,
    ) -> None:
        record_skepticism_challenge(
            state,
            source=source,
            reason=reason,
            evidence=evidence or {},
        )

    def _maybe_issue_skepticism_challenge(
        self,
        state: QuadraCodeState,
        message: ToolMessage,
    ) -> None:
        invariants = state.get("invariants")
        if isinstance(invariants, dict) and invariants.get("skepticism_gate_satisfied"):
            return
        excerpt = self._coerce_tool_content(message.content)[:280]
        evidence = {
            "tool": message.name,
            "tool_call_id": message.tool_call_id,
            "excerpt": excerpt,
        }
        self._record_skepticism_gate(
            state,
            source="tool_response",
            reason=message.name or "tool_output",
            evidence=evidence,
        )

    async def _flush_prp_metrics(self, state: QuadraCodeState) -> None:
        queued_events = list(state.get("prp_telemetry", []))
        if not queued_events:
            return
        state["prp_telemetry"] = []
        for record in queued_events:
            event = record.get("event", "prp_transition")
            payload = record.get("payload", {})
            if event == "invariant_violation":
                # Keep invariant telemetry internal/minimal to avoid reordering user-facing metrics
                continue
            await self.metrics.emit(state, event, payload)

    async def _emit_curation_metrics(
        self, state: QuadraCodeState, *, reason: str
    ) -> None:
        summary = state.get("last_curation_summary") or {}
        payload = {
            **summary,
            "reason": reason,
        }
        if "timestamp" not in payload:
            payload["timestamp"] = _utc_now().isoformat()
        if "total_segments" not in payload:
            payload["total_segments"] = len(state.get("context_segments", []))
        if "operation_counts" not in payload:
            payload["operation_counts"] = {}

        await self.metrics.emit(state, "curation", payload)
        state["last_curation_summary"] = {}

    async def _emit_load_metrics(self, state: QuadraCodeState) -> None:
        load_events = list(state.get("recent_loads", []))
        if not load_events:
            return
        payload = {
            "count": len(load_events),
            "segments": load_events,
            "context_window_used": state.get("context_window_used", 0),
        }
        await self.metrics.emit(state, "load", payload)
        state["recent_loads"] = []

    async def _emit_externalization_metrics(self, state: QuadraCodeState) -> None:
        events = list(state.get("recent_externalizations", []))
        if not events:
            return
        payload = {
            "count": len(events),
            "externalizations": events,
        }
        await self.metrics.emit(state, "externalize", payload)
        state["recent_externalizations"] = []

    def _recommendations_for_metric(
        self, metric: str, state: QuadraCodeState
    ) -> List[str]:
        # Check if we should use more aggressive compression based on context pressure
        context_ratio = self._context_ratio(state)
        pressure_modifier = self.config.prompt_templates.get_pressure_modifier(context_ratio)
        
        suggestions: Dict[str, List[str]] = {
            "relevance": [
                "prioritize high-priority segments in context",
                "trim low-priority history",
            ],
            "coherence": [
                "reorder context segments by timestamp",
                "summarize bridging content",
            ],
            "completeness": [
                "load missing context types",
                "refresh context hierarchy",
            ],
            "freshness": [
                "replace stale segments with recent data",
                "trigger progressive loader for latest artifacts",
            ],
            "diversity": [
                "pull complementary tools or docs",
                "expand background knowledge",
            ],
            "efficiency": [
                "compress verbose segments",
                "externalize archival content",
            ],
        }
        
        # Add adaptive compression recommendation if under pressure
        if context_ratio > 0.75 and metric == "efficiency":
            suggestions["efficiency"].append(f"Apply {self.config.compression_profile} compression profile")

        recs = suggestions.get(metric, [])
        if metric == "completeness":
            missing = self._missing_context_types(state)
            if missing:
                recs = recs + [f"load context types: {', '.join(sorted(missing))}"]
        return recs

    def _missing_context_types(self, state: QuadraCodeState) -> List[str]:
        expected = set(self.config.context_priorities.keys())
        present = {
            segment.get("type", "").split(":", 1)[0]
            for segment in state.get("context_segments", [])
        }
        return [item for item in expected if item not in present]

    def _deduplicate(self, values: List[str]) -> List[str]:
        seen = set()
        result: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    def _update_curation_rules(
        self, state: QuadraCodeState, reflection: ReflectionResult
    ) -> None:
        rules = state["curation_rules"]
        focus = reflection.focus_metric
        if focus and reflection.recommendations:
            rules.append(
                {
                    "metric": focus,
                    "applied_at": reflection.timestamp,
                    "actions": reflection.recommendations,
                    "quality": reflection.quality_score,
                }
            )
            if len(rules) > self.config.curation_rules_max:
                del rules[0]


class _NoOpExternalMemory:
    async def save_checkpoint(self, state: QuadraCodeState) -> str:
        return f"checkpoint-{len(state['memory_checkpoints']) + 1}"

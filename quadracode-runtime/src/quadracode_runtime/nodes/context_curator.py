"""
This module implements the `ContextCurator`, a key component of the Quadracode 
runtime's context engine, based on the principles of the MemAct framework.

The `ContextCurator` is responsible for dynamically managing the working context 
of the language model. It does this by applying a set of "context operations" 
(e.g., retain, compress, summarize, externalize) to the context segments. The 
curator's main goal is to keep the context size within a target limit while 
preserving the most relevant and important information. It uses a heuristic-based 
scoring system to evaluate the value of each context segment and then selects the 
most appropriate operation for each one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Tuple
from uuid import uuid4

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from ..config import ContextEngineConfig
from ..context_engine_logging import log_context_compression
from ..state import ContextEngineState, ContextSegment
from .context_operations import ContextOperation


LOGGER = logging.getLogger(__name__)


class ContextCurator:
    """
    Applies MemAct-inspired operations to manage the working context.

    This class implements the core logic for the context curation process. It 
    scores context segments, determines the optimal set of operations to apply, 
    and then executes those operations to produce a new, optimized set of 
    context segments. It also includes a learning mechanism that adjusts the 
    scores of the operations based on their impact on the context quality.

    Attributes:
        config: The configuration for the context engine.
        operation_history: A deque that keeps track of the most recent operations.
        operation_scores: A dictionary that stores the learned scores for each 
                          context operation.
    """

    def __init__(self, config: ContextEngineConfig) -> None:
        """
        Initializes the `ContextCurator`.

        Args:
            config: The configuration for the context engine.
        """
        self.config = config
        self.operation_history: Deque[str] = deque(maxlen=256)
        self.operation_scores: Dict[ContextOperation, float] = {
            ContextOperation.RETAIN: 0.8,
            ContextOperation.COMPRESS: 0.6,
            ContextOperation.SUMMARIZE: 0.6,
            ContextOperation.EXTERNALIZE: 0.5,
            ContextOperation.DISCARD: 0.4,
            ContextOperation.ISOLATE: 0.4,
            ContextOperation.REFLECT: 0.5,
            ContextOperation.CURATE: 0.5,
            ContextOperation.EVOLVE: 0.5,
            ContextOperation.FETCH: 0.5,
        }
        self._llm = None
        self._llm_lock = asyncio.Lock()

    async def optimize(self, state: ContextEngineState, target_tokens: int) -> ContextEngineState:
        """
        Runs the main context optimization routine for engineered segments.

        This method is executed before the driver makes a decision. It orchestrates 
        the entire curation process, from scoring the segments to applying the 
        operations and updating the state.

        Args:
            state: The current state of the context engine.
            target_tokens: The target token count for the segments.

        Returns:
            The updated state with the optimized context segments.
        """

        segments = list(state.get("context_segments", ()))
        if not segments:
            return state

        # Score the current context segments
        scores = await self._score_segments(segments)
        
        # Determine the best operation for each segment
        if self.config.curator_model in {"heuristic", "", None}:
            decisions = await self._determine_operations_heuristic(segments, scores, state, target_tokens)
        else:
            decisions = await self._determine_operations_llm(segments, scores, state, target_tokens)

        new_segments: List[ContextSegment] = []
        external_refs: List[Dict[str, str]] = []
        operation_counts: Dict[str, int] = {}

        # Apply the chosen operation to each segment
        for segment, operation in decisions:
            handler = self._operation_handler(operation)
            result = await handler(segment, state)

            operation_counts[operation.value] = operation_counts.get(operation.value, 0) + 1

            if result is None:
                continue

            if isinstance(result, tuple):
                segment, ref = result
                if ref:
                    external_refs.append(
                        {
                            "id": ref.get("id"),
                            "path": ref.get("path"),
                            "source_segment": segment.get("id"),
                            "source_type": segment.get("type"),
                            "source_tokens": segment.get("token_count", 0),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
            else:
                segment = result

            new_segments.append(segment)

        state["context_segments"] = new_segments
        if external_refs:
            state["external_memory_index"].update({ref["id"]: ref["path"] for ref in external_refs if ref.get("id")})
            state.setdefault("recent_externalizations", []).extend(external_refs)

        state["last_curation_summary"] = {
            "operation_counts": operation_counts,
            "total_segments": len(new_segments),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return state

    async def post_decision_curation(self, state: ContextEngineState) -> ContextEngineState:
        """
        Performs curation tasks after the driver has made a decision.

        This method is responsible for learning from the outcome of the driver's 
        decision and for cleaning up any stale context segments.

        Args:
            state: The current state of the context engine.

        Returns:
            The updated state.
        """

        await self._learn_from_outcome(state)
        # Remove stale isolated segments
        state["context_segments"] = [
            segment
            for segment in state.get("context_segments", [])
            if not self._is_stale(segment)
        ]

        return state

    async def _score_segments(self, segments: Iterable[ContextSegment]) -> List[float]:
        """Score segments using priority, recency, and length heuristics."""

        now = datetime.now(timezone.utc)
        scores: List[float] = []

        for segment in segments:
            priority = segment.get("priority", 5)
            token_count = max(segment.get("token_count", 1), 1)
            timestamp = segment.get("timestamp")
            recency = 1.0
            if timestamp:
                try:
                    delta = now - datetime.fromisoformat(timestamp)
                    recency = max(0.1, 1.0 - delta.total_seconds() / 86_400)
                except ValueError:
                    recency = 0.5

            efficiency = 1 / (1 + token_count / 256)
            score = (priority / 10) * 0.5 + recency * 0.3 + efficiency * 0.2
            scores.append(min(max(score, 0.0), 1.0))

        return scores

    async def _ensure_llm(self):
        """Lazy-load LLM for curator operations."""
        if self._llm is None:
            async with self._llm_lock:
                if self._llm is None:
                    self._llm = init_chat_model(self.config.curator_model)
        return self._llm

    async def _determine_operations_heuristic(
        self,
        segments: List[ContextSegment],
        scores: List[float],
        state: ContextEngineState,
        target_tokens: int,
    ) -> List[Tuple[ContextSegment, ContextOperation]]:
        """
        Determines the most appropriate context operation for each segment using heuristics.

        This method uses the segment scores, the current token count, and the 
        target context size to decide which operation to apply to each segment. 
        It prioritizes retaining high-priority segments and then applies other 
        operations (e.g., externalize, discard) to the remaining segments to 
        stay within the token limit.

        Args:
            segments: The list of context segments.
            scores: The corresponding scores for each segment.
            state: The current state of the context engine.
            target_tokens: The target token count for the segments.

        Returns:
            A list of tuples, each containing a segment and the chosen 
            operation.
        """

        current_tokens = sum(max(s.get("token_count", 0), 0) for s in segments)
        ranked = sorted(zip(segments, scores), key=lambda item: (item[0]["priority"], item[1]), reverse=True)

        decisions: List[Tuple[ContextSegment, ContextOperation]] = []
        tokens_retained = 0
        for segment, score in ranked:
            op = ContextOperation.RETAIN
            segment_tokens = max(segment.get("token_count", 0), 0)
            
            if tokens_retained + segment_tokens > target_tokens:
                # We are over budget, must take action
                if segment.get("priority", 1) >= self.config.min_segment_priority:
                    op = ContextOperation.EXTERNALIZE
                else:
                    op = ContextOperation.DISCARD
            elif score < 0.35 and segment.get("compression_eligible", True):
                op = ContextOperation.COMPRESS
            elif score < 0.55 and segment.get("compression_eligible", True):
                op = ContextOperation.SUMMARIZE
            elif score < 0.25:
                op = ContextOperation.DISCARD
            
            if op == ContextOperation.RETAIN:
                tokens_retained += segment_tokens

            decisions.append((segment, op))
            self.operation_history.append(op.value)

        return decisions

    async def _determine_operations_llm(
        self,
        segments: List[ContextSegment],
        scores: List[float],
        state: ContextEngineState,
        target_tokens: int,
    ) -> List[Tuple[ContextSegment, ContextOperation]]:
        """
        Determines the most appropriate context operation for each segment using LLM.

        This method asks the LLM to evaluate each segment and recommend the best
        operation based on current context, focus, and usage patterns.

        Args:
            segments: The list of context segments.
            scores: The corresponding scores for each segment.
            state: The current state of the context engine.
            target_tokens: The target token count for the segments.

        Returns:
            A list of tuples, each containing a segment and the chosen operation.
        """
        llm = await self._ensure_llm()
        prompts = self.config.prompt_templates
        
        current_tokens = sum(max(s.get("token_count", 0), 0) for s in segments)
        usage_ratio = (current_tokens / target_tokens) * 100 if target_tokens > 0 else 100
        
        focus = state.get("context_playbook", {}).get("last_reflection", {}).get("focus_metric", "quality")
        
        decisions: List[Tuple[ContextSegment, ContextOperation]] = []
        
        # Evaluate segments in batches to reduce LLM calls
        for segment, score in zip(segments, scores):
            segment_info = (
                f"ID: {segment['id']}\n"
                f"Type: {segment['type']}\n"
                f"Priority: {segment.get('priority', 5)}\n"
                f"Tokens: {segment.get('token_count', 0)}\n"
                f"Score: {score:.2f}\n"
                f"Content preview: {segment.get('content', '')[:200]}..."
            )
            
            prompt = prompts.get_prompt(
                "curator_operation_prompt",
                segment=segment_info,
                focus=focus,
                usage_ratio=f"{usage_ratio:.1f}"
            )
            
            response = await llm.ainvoke(
                [
                    SystemMessage(content=prompts.curator_system_prompt),
                    HumanMessage(content=prompt)
                ]
            )
            
            # Parse LLM response for operation recommendation
            response_text = str(response.content).lower()
            
            # Extract operation from response
            if "retain" in response_text and "retain" in response_text[:100]:
                op = ContextOperation.RETAIN
            elif "compress" in response_text and "compress" in response_text[:100]:
                op = ContextOperation.COMPRESS
            elif "summarize" in response_text and "summarize" in response_text[:100]:
                op = ContextOperation.SUMMARIZE
            elif "externalize" in response_text and "externalize" in response_text[:100]:
                op = ContextOperation.EXTERNALIZE
            elif "discard" in response_text and "discard" in response_text[:100]:
                op = ContextOperation.DISCARD
            else:
                # Fallback to heuristic if LLM response is unclear
                LOGGER.warning("Unclear curator LLM response for segment %s, using heuristic", segment["id"])
                if score > 0.7:
                    op = ContextOperation.RETAIN
                elif score < 0.3:
                    op = ContextOperation.DISCARD
                else:
                    op = ContextOperation.COMPRESS
            
            decisions.append((segment, op))
            self.operation_history.append(op.value)
        
        return decisions

    def _operation_handler(self, operation: ContextOperation):
        """Returns the handler function for a given context operation."""
        mapping = {
            ContextOperation.RETAIN: self._handle_retain,
            ContextOperation.COMPRESS: self._handle_compress,
            ContextOperation.SUMMARIZE: self._handle_summarize,
            ContextOperation.EXTERNALIZE: self._handle_externalize,
            ContextOperation.DISCARD: self._handle_discard,
            ContextOperation.ISOLATE: self._handle_isolate,
            ContextOperation.REFLECT: self._handle_retain,
            ContextOperation.EVOLVE: self._handle_retain,
            ContextOperation.CURATE: self._handle_retain,
            ContextOperation.FETCH: self._handle_retain,
        }
        return mapping[operation]

    async def _handle_retain(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> ContextSegment:
        """Handler for the RETAIN operation (no-op)."""
        return segment

    async def _handle_discard(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> None:
        """Handler for the DISCARD operation."""
        return None

    async def _handle_compress(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> ContextSegment:
        """Handler for the COMPRESS operation."""
        before_tokens = max(int(segment.get("token_count", 0) or 0), 0)
        before_content = segment.get("content", "")
        compressed = self._compress_segment(segment)
        compressed["compression_eligible"] = False
        after_tokens = compressed.get("token_count", before_tokens)
        
        await log_context_compression(
            state,
            action="compress",
            stage="context_curator.optimize",
            reason="curator_compress",
            segment_id=segment.get("id"),
            segment_type=segment.get("type"),
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            before_content=before_content,
            after_content=compressed.get("content", ""),
            metadata={
                "operation": "compress",
                "priority": segment.get("priority"),
                "compression_eligible": segment.get("compression_eligible", True),
            },
        )
        
        # Update state with compression event for UI visibility
        compression_event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "segment_id": segment.get("id"),
            "segment_type": segment.get("type"),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "tokens_saved": before_tokens - after_tokens,
            "compression_ratio": float(after_tokens / before_tokens) if before_tokens > 0 else 1.0,
            "action": "compress",
            "stage": "context_curator.optimize",
        }
        
        # Add to recent compressions list (keep last 10)
        if "recent_compressions" not in state:
            state["recent_compressions"] = []
        state["recent_compressions"].append(compression_event)
        if len(state["recent_compressions"]) > 10:
            state["recent_compressions"] = state["recent_compressions"][-10:]
        
        # Update latest compression event
        state["last_compression_event"] = compression_event
        
        return compressed

    async def _handle_summarize(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> ContextSegment:
        """Handler for the SUMMARIZE operation."""
        before_tokens = max(int(segment.get("token_count", 0) or 0), 0)
        before_content = segment.get("content", "")
        summary = self._summarize_segment(segment)
        summary["restorable_reference"] = segment.get("id")
        summary["compression_eligible"] = False
        summary["type"] = f"summary:{segment['type']}"
        after_tokens = summary.get("token_count", before_tokens)
        
        await log_context_compression(
            state,
            action="summarize",
            stage="context_curator.optimize",
            reason="curator_summarize",
            segment_id=segment.get("id"),
            segment_type=segment.get("type"),
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            before_content=before_content,
            after_content=summary.get("content", ""),
            metadata={
                "operation": "summarize",
                "priority": segment.get("priority"),
                "compression_eligible": segment.get("compression_eligible", True),
            },
        )
        
        # Update state with compression event for UI visibility
        compression_event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "segment_id": segment.get("id"),
            "segment_type": segment.get("type"),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "tokens_saved": before_tokens - after_tokens,
            "compression_ratio": float(after_tokens / before_tokens) if before_tokens > 0 else 1.0,
            "action": "summarize",
            "stage": "context_curator.optimize",
        }
        
        # Add to recent compressions list (keep last 10)
        if "recent_compressions" not in state:
            state["recent_compressions"] = []
        state["recent_compressions"].append(compression_event)
        if len(state["recent_compressions"]) > 10:
            state["recent_compressions"] = state["recent_compressions"][-10:]
        
        # Update latest compression event
        state["last_compression_event"] = compression_event
        
        return summary

    async def _handle_externalize(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> Tuple[ContextSegment, Dict[str, str]]:
        """Handler for the EXTERNALIZE operation."""
        pointer, reference = await asyncio.to_thread(self._externalize_segment, segment)
        return pointer, reference

    async def _handle_isolate(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> ContextSegment:
        """Handler for the ISOLATE operation."""
        isolated = dict(segment)
        isolated["priority"] = max(1, isolated.get("priority", 1) - 1)
        return isolated

    async def _learn_from_outcome(self, state: ContextEngineState) -> None:
        """
        Adjusts the scores of the context operations based on the quality of 
        the context.
        """
        quality = state.get("context_quality_score", 0.5)
        adjustment = (quality - 0.5) * self.config.operation_learning_rate
        for op in self.operation_scores:
            baseline = self.operation_scores[op]
            self.operation_scores[op] = min(max(baseline + adjustment, 0.0), 1.0)

    def _truncate(self, text: str, limit: int) -> str:
        """Truncates a string to a given limit."""
        if len(text) <= limit:
            return text
        return f"{text[:limit]}…"

    # --- Type-specific pipelines -------------------------------------------------

    def _compress_segment(self, segment: ContextSegment) -> ContextSegment:
        handlers = {
            "conversation": self._compress_conversation,
            "tool_output": self._compress_tool_output,
            "memory": self._compress_memory,
        }
        handler = handlers.get(segment.get("type"))
        if handler:
            return handler(segment)

        compressed = dict(segment)
        compressed["content"] = self._truncate(segment["content"], 200)
        compressed["token_count"] = max(1, compressed.get("token_count", 1) // 2)
        return compressed

    def _summarize_segment(self, segment: ContextSegment) -> ContextSegment:
        handlers = {
            "conversation": self._summarize_conversation,
            "tool_output": self._summarize_tool_output,
            "memory": self._summarize_memory,
        }
        handler = handlers.get(segment.get("type"))
        if handler:
            return handler(segment)

        summary = dict(segment)
        summary["content"] = self._simple_summary(segment["content"])
        summary["token_count"] = max(1, summary.get("token_count", 1) // 4)
        return summary

    def _externalize_segment(self, segment: ContextSegment) -> Tuple[ContextSegment, Dict[str, str]]:
        ref_id = f"ext-{uuid4().hex}"
        pointer = dict(segment)
        pointer["content"] = self._truncate(segment["content"], 160)
        pointer["token_count"] = max(1, pointer.get("token_count", 1) // 8)
        pointer["type"] = f"pointer:{segment['type']}"
        pointer["restorable_reference"] = ref_id

        path = self._build_external_path(segment, ref_id)
        self._persist_external_segment(path, segment)
        return pointer, {"id": ref_id, "path": path}

    # --- Compression helpers -----------------------------------------------------

    def _compress_conversation(self, segment: ContextSegment) -> ContextSegment:
        compressed = dict(segment)
        lines = segment["content"].splitlines()
        tail = lines[-6:]
        compressed["content"] = "\n".join(tail)
        compressed["token_count"] = max(1, compressed.get("token_count", len(tail) * 16) // 2)
        return compressed

    def _compress_tool_output(self, segment: ContextSegment) -> ContextSegment:
        compressed = dict(segment)
        content = segment["content"]
        compressed["content"] = self._truncate(content, 400)
        compressed["token_count"] = max(1, compressed.get("token_count", 1) // 3)
        return compressed

    def _compress_memory(self, segment: ContextSegment) -> ContextSegment:
        compressed = dict(segment)
        bullet_points = [line.strip() for line in segment["content"].split("\n") if line.strip()]
        compressed["content"] = "\n".join(bullet_points[:5])
        compressed["token_count"] = max(1, compressed.get("token_count", 1) // 3)
        return compressed

    # --- Summarization helpers ---------------------------------------------------

    def _summarize_conversation(self, segment: ContextSegment) -> ContextSegment:
        summary = dict(segment)
        lines = [line for line in segment["content"].splitlines() if line.strip()]
        turns = [line.split(":", 1) for line in lines if ":" in line]
        clipped = []
        for speaker, message in turns[:8]:
            clipped.append(f"{speaker.strip()}: {self._truncate(message.strip(), 80)}")
        summary["content"] = "Conversation summary:\n" + "\n".join(clipped)
        summary["token_count"] = max(1, summary.get("token_count", len(clipped) * 10) // 4)
        return summary

    def _summarize_tool_output(self, segment: ContextSegment) -> ContextSegment:
        summary = dict(segment)
        lines = [line.strip() for line in segment["content"].splitlines() if line.strip()]
        summary_lines = lines[:3]
        summary["content"] = "Tool output summary:\n" + "\n".join(summary_lines)
        summary["token_count"] = max(1, summary.get("token_count", 1) // 5)
        return summary

    def _summarize_memory(self, segment: ContextSegment) -> ContextSegment:
        summary = dict(segment)
        summary["content"] = self._simple_summary(segment["content"], prefix="Memory summary: ")
        summary["token_count"] = max(1, summary.get("token_count", 1) // 5)
        return summary

    def _simple_summary(self, text: str, prefix: str = "Summary: ") -> str:
        if not text:
            return prefix
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        base = paragraphs[0] if paragraphs else text
        return prefix + self._truncate(base, 180)

    # --- Externalization helpers -------------------------------------------------

    def _build_external_path(self, segment: ContextSegment, ref_id: str) -> str:
        root = Path(self.config.external_memory_path or ".")
        type_dir = segment.get("type", "generic").replace(":", "_")
        filename = f"{segment['id']}-{ref_id}.json"
        return str(root.joinpath(type_dir, filename))

    def _persist_external_segment(self, path_str: str, segment: ContextSegment) -> None:
        if not self.config.externalize_write_enabled:
            return
        target = Path(path_str)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "segment": segment,
                "persisted_at": datetime.now(timezone.utc).isoformat(),
                "version": 1,
            }
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("Failed to persist externalized segment %s: %s", path_str, exc)

    def _is_stale(self, segment: ContextSegment) -> bool:
        """Determines if a context segment is stale."""
        timestamp = segment.get("timestamp")
        if not timestamp:
            return False
        try:
            delta = datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)
        except ValueError:
            return False
        return delta.total_seconds() > 86_400 * 7  # 7 days

"""Context curation implementation based on MemAct."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Tuple
from uuid import uuid4

from ..config import ContextEngineConfig
from ..state import ContextEngineState, ContextSegment
from .context_operations import ContextOperation


LOGGER = logging.getLogger(__name__)


class ContextCurator:
    """Applies MemAct operations to manage working context."""

    def __init__(self, config: ContextEngineConfig) -> None:
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

    async def optimize(self, state: ContextEngineState) -> ContextEngineState:
        """Main optimization routine executed before driver decisions."""

        segments = list(state.get("context_segments", ()))
        if not segments:
            return state

        scores = await self._score_segments(segments)
        decisions = await self._determine_operations(segments, scores, state)

        new_segments: List[ContextSegment] = []
        external_refs: List[Dict[str, str]] = []
        operation_counts: Dict[str, int] = {}

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
        """Adjust state after the driver has produced an action."""

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

    async def _determine_operations(
        self,
        segments: List[ContextSegment],
        scores: List[float],
        state: ContextEngineState,
    ) -> List[Tuple[ContextSegment, ContextOperation]]:
        """Pick operations per segment using current targets and learned weights."""

        target_tokens = self.config.target_context_size
        current_tokens = 0
        ranked = sorted(zip(segments, scores), key=lambda item: (item[0]["priority"], item[1]), reverse=True)

        decisions: List[Tuple[ContextSegment, ContextOperation]] = []
        for segment, score in ranked:
            op = ContextOperation.RETAIN
            segment_tokens = max(segment.get("token_count", 0), 0)
            current_tokens += segment_tokens

            if current_tokens > target_tokens:
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

            decisions.append((segment, op))
            self.operation_history.append(op.value)

        return decisions

    def _operation_handler(self, operation: ContextOperation):
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
        return segment

    async def _handle_discard(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> None:
        return None

    async def _handle_compress(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> ContextSegment:
        compressed = self._compress_segment(segment)
        compressed["compression_eligible"] = False
        return compressed

    async def _handle_summarize(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> ContextSegment:
        summary = self._summarize_segment(segment)
        summary["restorable_reference"] = segment.get("id")
        summary["compression_eligible"] = False
        summary["type"] = f"summary:{segment['type']}"
        return summary

    async def _handle_externalize(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> Tuple[ContextSegment, Dict[str, str]]:
        pointer, reference = self._externalize_segment(segment)
        return pointer, reference

    async def _handle_isolate(
        self, segment: ContextSegment, state: ContextEngineState
    ) -> ContextSegment:
        isolated = dict(segment)
        isolated["priority"] = max(1, isolated.get("priority", 1) - 1)
        return isolated

    async def _learn_from_outcome(self, state: ContextEngineState) -> None:
        quality = state.get("context_quality_score", 0.5)
        adjustment = (quality - 0.5) * self.config.operation_learning_rate
        for op in self.operation_scores:
            baseline = self.operation_scores[op]
            self.operation_scores[op] = min(max(baseline + adjustment, 0.0), 1.0)

    def _truncate(self, text: str, limit: int) -> str:
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
        timestamp = segment.get("timestamp")
        if not timestamp:
            return False
        try:
            delta = datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)
        except ValueError:
            return False
        return delta.total_seconds() > 86_400 * 7  # 7 days

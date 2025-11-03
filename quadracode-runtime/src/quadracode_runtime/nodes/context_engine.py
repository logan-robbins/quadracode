"""Core context engineering node implementation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from ..config import ContextEngineConfig
from ..autonomous import process_autonomous_tool_response
from ..state import (
    ContextEngineState,
    ContextSegment,
    make_initial_context_engine_state,
)
from ..metrics import ContextMetricsEmitter
from .context_curator import ContextCurator
from .context_operations import ContextOperation
from .context_reducer import ContextReducer
from .context_scorer import ContextScorer
from .progressive_loader import ProgressiveContextLoader


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
    """High-level coordinator for context engineering flows."""

    def __init__(self, config: ContextEngineConfig):
        self.config = config
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

    def pre_process_sync(self, state: ContextEngineState) -> ContextEngineState:
        return asyncio.run(self.pre_process(state))

    def post_process_sync(self, state: ContextEngineState) -> ContextEngineState:
        return asyncio.run(self.post_process(state))

    def govern_context_sync(self, state: ContextEngineState) -> ContextEngineState:
        return asyncio.run(self.govern_context(state))

    def handle_tool_response_sync(
        self, state: ContextEngineState
    ) -> ContextEngineState:
        tool_messages = self._extract_tool_messages(state)
        if not tool_messages:
            return asyncio.run(self.handle_tool_response(state, None))
        return asyncio.run(self._handle_tool_messages(state, tool_messages))

    async def pre_process(self, state: ContextEngineState) -> ContextEngineState:
        state = self._ensure_state_defaults(state)
        quality_score = await self.scorer.evaluate(state)
        state["context_quality_score"] = quality_score

        if quality_score < self.config.quality_threshold:
            state = await self.curator.optimize(state)
            state = self._recompute_context_usage(state)
            await self._emit_curation_metrics(state, reason="quality_recovery")
            await self._emit_externalization_metrics(state)

        overflow = state["context_window_used"]
        max_tokens = state.get("context_window_max", self.config.context_window_max)

        if overflow > min(max_tokens, self.config.target_context_size):
            state = await self.curator.optimize(state)
            state = self._recompute_context_usage(state)
            await self._emit_curation_metrics(state, reason="overflow_control")
            await self._emit_externalization_metrics(state)

        state = await self.loader.prepare_context(state)
        state = self._recompute_context_usage(state)
        state = await self._enforce_limits(state)
        await self._emit_load_metrics(state)
        await self.metrics.emit(
            state,
            "pre_process",
            {
                "quality_score": quality_score,
                "quality_components": state.get("context_quality_components", {}),
                "context_window_used": state.get("context_window_used", 0),
                "context_window_max": state.get("context_window_max", self.config.context_window_max),
            },
        )
        return state

    async def post_process(self, state: ContextEngineState) -> ContextEngineState:
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

        await self.metrics.emit(
            state,
            "post_process",
            {
                "quality_score": state.get("context_quality_score", 0.0),
                "focus_metric": reflection_payload.focus_metric,
                "issues": reflection_payload.issues,
                "recommendations": reflection_payload.recommendations,
                "context_window_used": state.get("context_window_used", 0),
            },
        )
        return state

    async def govern_context(self, state: ContextEngineState) -> ContextEngineState:
        state = self._ensure_state_defaults(state)
        plan = await self._generate_governor_plan(state)
        state = await self._apply_governor_plan(state, plan)
        return state

    async def handle_tool_response(
        self, state: ContextEngineState, tool_response: Any | None
    ) -> ContextEngineState:
        state = self._ensure_state_defaults(state)
        autonomous_event = None
        if tool_response is not None:
            state, autonomous_event = process_autonomous_tool_response(state, tool_response)
        if tool_response is None:
            return state
        scoring_payload: Any
        if isinstance(tool_response, ToolMessage):
            scoring_payload = self._render_tool_message(tool_response)
        else:
            scoring_payload = tool_response
        relevance = await self.scorer.score_tool_output(scoring_payload)
        operation = await self._decide_operation(relevance, state)
        state = await self._apply_operation(state, tool_response, operation)
        state = await self._enforce_limits(state)
        await self.metrics.emit(
            state,
            "tool_response",
            {
                "operation": operation.value,
                "relevance": relevance,
                "segment_count": len(state.get("context_segments", [])),
            },
        )
        if autonomous_event:
            await self.metrics.emit_autonomous(
                autonomous_event["event"],
                autonomous_event.get("payload", {}),
            )
        return state

    async def _decide_operation(
        self, relevance: float, state: ContextEngineState
    ) -> ContextOperation:
        if relevance < 0.2:
            return ContextOperation.DISCARD
        if relevance < 0.5:
            return ContextOperation.SUMMARIZE
        return ContextOperation.RETAIN

    async def _apply_operation(
        self,
        state: ContextEngineState,
        tool_response: Any,
        operation: ContextOperation,
    ) -> ContextEngineState:
        segment = self._build_segment_from_tool(tool_response, state)

        if operation is ContextOperation.DISCARD:
            return state

        needs_reduction = (
            operation in {ContextOperation.SUMMARIZE, ContextOperation.COMPRESS}
            or len(segment["content"]) > self.max_tool_payload_chars
        )

        if needs_reduction:
            reduced = await self.reducer.reduce(
                segment["content"], focus=segment.get("type")
            )
            segment["content"] = reduced.content
            segment["token_count"] = reduced.token_count
            segment["restorable_reference"] = segment.get("restorable_reference") or segment["id"]

        state["context_segments"].append(segment)
        state["working_memory"][segment["id"]] = segment
        state = self._recompute_context_usage(state)
        return state

    def _build_segment_from_tool(
        self, tool_response: Any, state: ContextEngineState
    ) -> ContextSegment:
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
        self, state: ContextEngineState, tool_messages: List[ToolMessage]
    ) -> ContextEngineState:
        current = state
        for message in tool_messages:
            current = await self.handle_tool_response(current, message)
        return current

    def _extract_tool_messages(
        self, state: ContextEngineState
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

    async def _generate_governor_plan(self, state: ContextEngineState) -> Dict[str, Any]:
        override = state.pop("governor_plan_override", None)
        if override:
            return override

        if self.governor_model in {"heuristic", ""}:
            return self._heuristic_governor_plan(state)

        llm = await self._ensure_governor_llm()
        snapshot = self._plan_segments_snapshot(state)
        summary = self._plan_context_summary(state, snapshot)

        system_prompt = (
            "You are the context governor for a long-running AI agent. "
            "Your job is to keep the context window focused, concise, and free of conflicts."
        )
        instructions = (
            "Review the provided JSON summary. Produce a strict JSON object with keys "
            "'actions' and 'prompt_outline'. Each action must include 'segment_id' and 'decision'"
            " (retain, compress, summarize, isolate, externalize, discard). Optionally include "
            "'priority' or 'focus'. The prompt_outline should contain optional 'system', 'focus',"
            " and 'ordered_segments'. Do not include any additional prose."
        )

        payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"{instructions}\n\nINPUT:\n```json\n{payload}\n```"),
        ]
        response = await asyncio.to_thread(llm.invoke, messages)
        return self._parse_plan_response(response.content)

    async def _apply_governor_plan(
        self, state: ContextEngineState, plan: Dict[str, Any]
    ) -> ContextEngineState:
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
                pointer, reference = self.curator._externalize_segment(segment_copy)
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

    async def _ensure_governor_llm(self):
        if self.governor_model in {"heuristic", ""}:
            raise RuntimeError("Heuristic governor does not use an LLM")
        if self._governor_llm is not None:
            return self._governor_llm

        async with self._governor_lock:
            if self._governor_llm is None:
                self._governor_llm = init_chat_model(self.governor_model)
        return self._governor_llm

    def _heuristic_governor_plan(self, state: ContextEngineState) -> Dict[str, Any]:
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

        outline = {
            "system": "Heuristic context governor active. Maintain focus on current objectives and recent decisions.",
            "focus": state.get("pending_context", []) or state.get("context_playbook", {}).get("last_reflection", {}).get("focus_metric"),
            "ordered_segments": [segment.get("id") for segment in sorted_segments[:max_segments] if segment.get("id")],
        }

        return {
            "actions": actions,
            "prompt_outline": outline,
        }

    def _plan_segments_snapshot(self, state: ContextEngineState) -> List[Dict[str, Any]]:
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
        self, state: ContextEngineState, segments: List[Dict[str, Any]]
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

    async def _reflect_on_decision(self, state: ContextEngineState) -> ReflectionResult:
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
        self, state: ContextEngineState, reflection: ReflectionResult
    ) -> ContextEngineState:
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
        self, state: ContextEngineState
    ) -> ContextEngineState:
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

    def _should_checkpoint(self, state: ContextEngineState) -> bool:
        frequency = self.config.checkpoint_frequency
        if not frequency:
            return False

        iteration = state.get("context_playbook", {}).get("iterations", 0)
        return iteration > 0 and iteration % frequency == 0

    def _ensure_state_defaults(
        self, state: ContextEngineState
    ) -> ContextEngineState:
        defaults = make_initial_context_engine_state(
            context_window_max=self.config.context_window_max
        )
        for key, default_value in defaults.items():
            if key not in state or state[key] is None:
                state[key] = default_value  # type: ignore[index]

        if not state["context_window_max"]:
            state["context_window_max"] = self.config.context_window_max

        return state

    @staticmethod
    def _truncate_content(content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        return f"{content[:limit]}…"

    def _recompute_context_usage(
        self, state: ContextEngineState
    ) -> ContextEngineState:
        total = sum(
            max(segment.get("token_count", 0), 0)
            for segment in state.get("context_segments", [])
        )
        state["context_window_used"] = int(total)
        return state

    async def _emit_curation_metrics(
        self, state: ContextEngineState, *, reason: str
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

    async def _emit_load_metrics(self, state: ContextEngineState) -> None:
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

    async def _emit_externalization_metrics(self, state: ContextEngineState) -> None:
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
        self, metric: str, state: ContextEngineState
    ) -> List[str]:
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

        recs = suggestions.get(metric, [])
        if metric == "completeness":
            missing = self._missing_context_types(state)
            if missing:
                recs = recs + [f"load context types: {', '.join(sorted(missing))}"]
        return recs

    def _missing_context_types(self, state: ContextEngineState) -> List[str]:
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
        self, state: ContextEngineState, reflection: ReflectionResult
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
    async def save_checkpoint(self, state: ContextEngineState) -> str:
        return f"checkpoint-{len(state['memory_checkpoints']) + 1}"

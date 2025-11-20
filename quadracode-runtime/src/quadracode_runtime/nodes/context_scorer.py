"""
This module implements the `ContextScorer`, a component of the context engine 
that evaluates the quality of the working context based on the principles of the 
ACE (Adaptive Context Engineering) framework.

The `ContextScorer` uses a set of lightweight, heuristic-based metrics to assess 
the context's relevance, coherence, completeness, freshness, diversity, and 
efficiency. These individual scores are then combined into a single, weighted 
quality score. This score is a critical signal for the `ContextCurator`, which 
uses it to trigger optimization routines when the context quality drops below a 
predefined threshold.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import re
from typing import Dict, Iterable, List

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from ..config import ContextEngineConfig
from ..state import ContextEngineState, ContextSegment


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ScoreBreakdown:
    """
    Represents the breakdown of the context quality score into its individual 
    components.
    """
    relevance: float
    coherence: float
    completeness: float
    freshness: float
    diversity: float
    efficiency: float


class ContextScorer:
    """
    Evaluates context quality using a set of lightweight, ACE-inspired 
    heuristics.

    This class provides the `evaluate` method, which is the main entry point for 
    the scoring process. It calculates each of the quality components and then 
    combines them into a single, weighted score. The weights can be dynamically 
    adjusted based on the current phase of the task.

    Attributes:
        config: The configuration for the context engine.
    """

    def __init__(self, config: ContextEngineConfig) -> None:
        """
        Initializes the `ContextScorer`.

        Args:
            config: The configuration for the context engine.
        """
        self.config = config
        self._llm = None
        self._llm_lock = asyncio.Lock()

    async def evaluate(self, state: ContextEngineState) -> float:
        """
        Evaluates the overall quality of the context.

        This is the main public method of the `ContextScorer`. It orchestrates 
        the calculation of all the individual quality components and then 
        combines them into a final, weighted score.

        Args:
            state: The current state of the context engine.

        Returns:
            A quality score between 0.0 and 1.0.
        """
        if self.config.scorer_model in {"heuristic", "", None}:
            return await self._evaluate_heuristic(state)
        else:
            return await self._evaluate_llm(state)

    async def _evaluate_heuristic(self, state: ContextEngineState) -> float:
        """Evaluate context quality using heuristic-based metrics."""
        segments = state.get("context_segments", [])

        breakdown = ScoreBreakdown(
            relevance=await self._score_relevance(segments, state),
            coherence=await self._score_coherence(segments),
            completeness=await self._score_completeness(segments, state),
            freshness=await self._score_freshness(segments),
            diversity=await self._score_diversity(segments),
            efficiency=await self._score_efficiency(state),
        )

        state["context_quality_components"] = asdict(breakdown)

        weights = self._get_phase_weights(state)
        total_weight = sum(weights.values()) or 1.0

        total_score = (
            breakdown.relevance * weights["relevance"]
            + breakdown.coherence * weights["coherence"]
            + breakdown.completeness * weights["completeness"]
            + breakdown.freshness * weights["freshness"]
            + breakdown.diversity * weights["diversity"]
            + breakdown.efficiency * weights["efficiency"]
        ) / total_weight

        return max(0.0, min(total_score, 1.0))

    async def _ensure_llm(self):
        """Lazy-load LLM for scoring operations."""
        if self._llm is None:
            async with self._llm_lock:
                if self._llm is None:
                    self._llm = init_chat_model(self.config.scorer_model)
        return self._llm

    async def _evaluate_llm(self, state: ContextEngineState) -> float:
        """Evaluate context quality using LLM-based analysis."""
        llm = await self._ensure_llm()
        prompts = self.config.prompt_templates
        
        segments = state.get("context_segments", [])
        
        # Build context summary for LLM evaluation
        context_summary = []
        for seg in segments[:10]:  # Limit to first 10 segments to avoid token overflow
            context_summary.append(
                f"[{seg['type']}] {seg['id']} (priority={seg.get('priority', 5)}, "
                f"tokens={seg.get('token_count', 0)})\n"
                f"Preview: {seg.get('content', '')[:150]}...\n"
            )
        
        context_text = "\n".join(context_summary)
        
        prompt = prompts.get_prompt(
            "scorer_evaluation_prompt",
            context=context_text
        )
        
        response = await asyncio.to_thread(
            llm.invoke,
            [
                SystemMessage(content=prompts.scorer_system_prompt),
                HumanMessage(content=prompt)
            ]
        )
        
        # Parse JSON response with scores
        response_text = str(response.content)
        
        try:
            # Try to extract JSON from response
            json_match = re.search(r'\{[^}]+\}', response_text)
            if json_match:
                scores_dict = json.loads(json_match.group(0))
                
                breakdown = ScoreBreakdown(
                    relevance=float(scores_dict.get("relevance", 0.5)),
                    coherence=float(scores_dict.get("coherence", 0.5)),
                    completeness=float(scores_dict.get("completeness", 0.5)),
                    freshness=float(scores_dict.get("freshness", 0.5)),
                    diversity=float(scores_dict.get("diversity", 0.5)),
                    efficiency=float(scores_dict.get("efficiency", 0.5)),
                )
            else:
                # Fallback to heuristic if JSON parsing fails
                LOGGER.warning("Could not parse LLM scorer response, falling back to heuristic")
                return await self._evaluate_heuristic(state)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            LOGGER.warning(f"Error parsing LLM scorer response: {e}, falling back to heuristic")
            return await self._evaluate_heuristic(state)
        
        state["context_quality_components"] = asdict(breakdown)
        
        weights = self._get_phase_weights(state)
        total_weight = sum(weights.values()) or 1.0
        
        total_score = (
            breakdown.relevance * weights["relevance"]
            + breakdown.coherence * weights["coherence"]
            + breakdown.completeness * weights["completeness"]
            + breakdown.freshness * weights["freshness"]
            + breakdown.diversity * weights["diversity"]
            + breakdown.efficiency * weights["efficiency"]
        ) / total_weight
        
        return max(0.0, min(total_score, 1.0))

    async def score_tool_output(self, tool_output: object) -> float:
        """
        Scores the relevance of a tool's output.

        This method provides a simple, heuristic-based score for a tool's 
        output based on its length.

        Args:
            tool_output: The output from the tool.

        Returns:
            A relevance score between 0.0 and 1.0.
        """
        if tool_output is None:
            return 0.0

        text = str(tool_output).strip()
        if not text:
            return 0.0

        length = len(text)
        if length < 40:
            return 0.3
        if length < 200:
            return 0.6
        return 0.8

    async def _score_relevance(
        self, segments: Iterable[ContextSegment], state: ContextEngineState
    ) -> float:
        """Calculates the relevance score of the context."""
        segments = list(segments)
        if not segments:
            return 1.0

        goal_text = self._determine_goal_text(state)
        goal_embedding = self._embed_text(goal_text)
        if not goal_embedding:
            return 1.0

        priorities = self.config.context_priorities
        total_weight = 0.0
        cumulative = 0.0

        for segment in segments:
            segment_type = segment.get("type", "generic").split(":", 1)[0]
            priority_hint = priorities.get(segment_type, segment.get("priority", 5))
            priority = max(segment.get("priority", priority_hint), priority_hint)
            weight = min(priority, 10)
            content = segment.get("content") or ""
            similarity = self._cosine_similarity(goal_embedding, self._embed_text(content))
            cumulative += similarity * weight
            total_weight += weight

        if total_weight == 0:
            return 1.0
        return max(0.0, min(1.0, cumulative / total_weight))

    async def _score_coherence(self, segments: Iterable[ContextSegment]) -> float:
        """Calculates the coherence score of the context."""
        segments = list(segments)
        if len(segments) < 2:
            return 1.0

        scores: List[float] = []
        for prev, current in zip(segments, segments[1:]):
            priority_gap = abs(prev.get("priority", 5) - current.get("priority", 5)) / 10
            time_gap = self._time_gap(prev.get("timestamp"), current.get("timestamp"))
            penalty = min(1.0, (priority_gap + time_gap) / 2)
            scores.append(max(0.0, 1.0 - penalty))

        return sum(scores) / len(scores) if scores else 1.0

    async def _score_completeness(
        self, segments: Iterable[ContextSegment], state: ContextEngineState
    ) -> float:
        """Calculates the completeness score of the context."""
        expected_types = set(self.config.context_priorities.keys())
        if not expected_types:
            return 1.0

        present = {segment.get("type", "").split(":", 1)[0] for segment in segments}

        coverage = len(expected_types & present) / len(expected_types)
        hierarchy_bonus = 0.0
        if state.get("context_hierarchy"):
            hits = sum(1 for key in expected_types if key in state["context_hierarchy"])
            hierarchy_bonus = hits / len(expected_types) * 0.1

        return min(1.0, coverage + hierarchy_bonus)

    async def _score_freshness(self, segments: Iterable[ContextSegment]) -> float:
        """Calculates the freshness score of the context."""
        segments = list(segments)
        if not segments:
            return 1.0

        now = datetime.now(timezone.utc)
        horizon = timedelta(hours=12)
        scores: List[float] = []
        for segment in segments:
            timestamp = self._parse_timestamp(segment.get("timestamp"))
            if not timestamp:
                scores.append(0.5)
                continue

            age = now - timestamp
            if age <= timedelta(0):
                scores.append(1.0)
            else:
                freshness = max(0.0, 1.0 - age / horizon)
                scores.append(float(freshness))

        return sum(scores) / len(scores) if scores else 1.0

    async def _score_diversity(self, segments: Iterable[ContextSegment]) -> float:
        """Calculates the diversity score of the context."""
        segments = list(segments)
        if not segments:
            return 1.0

        types = {segment.get("type", "").split(":", 1)[0] for segment in segments}
        diversity = len(types) / len(segments)
        return min(1.0, diversity)

    async def _score_efficiency(self, state: ContextEngineState) -> float:
        """Calculates the efficiency score of the context."""
        used = state.get("context_window_used", 0)
        max_allowed = state.get("context_window_max", self.config.context_window_max)
        if max_allowed <= 0:
            return 1.0

        # Optimal usage is ~70-85%
        lower_bound = max_allowed * 0.7
        upper_bound = max_allowed * 0.85

        if used < lower_bound:
            return used / lower_bound if lower_bound else 1.0
        if used > upper_bound:
            overflow = min(used - upper_bound, max_allowed * 0.3)
            return max(0.0, 1.0 - overflow / (max_allowed * 0.3))
        return 1.0

    def _get_phase_weights(self, state: ContextEngineState) -> Dict[str, float]:
        """
        Dynamically adjusts the scoring weights based on the current phase of 
        the task.
        """
        weights = dict(self.config.scoring_weights)
        phase = (state.get("current_phase") or "").lower()
        playbook_phase = state.get("context_playbook", {}).get("current_phase")
        if playbook_phase:
            phase = str(playbook_phase).lower()

        if "research" in phase or "plan" in phase:
            weights["diversity"] += 0.05
            weights["freshness"] += 0.05
        elif "build" in phase or "implementation" in phase:
            weights["relevance"] += 0.05
            weights["coherence"] += 0.05
        elif "test" in phase or "validate" in phase:
            weights["completeness"] += 0.05
            weights["efficiency"] += 0.05

        total = sum(weights.values()) or 1.0
        return {k: v / total for k, v in weights.items()}

    def _time_gap(self, a: str | None, b: str | None) -> float:
        """Calculates the time gap between two timestamps."""
        first = self._parse_timestamp(a)
        second = self._parse_timestamp(b)
        if not first or not second:
            return 0.5
        delta = abs((second - first).total_seconds())
        return min(1.0, delta / (3600 * 4))  # 4-hour horizon

    def _parse_timestamp(self, value: str | None) -> datetime | None:
        """Safely parses an ISO-8601 formatted timestamp string."""
        if not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            ts = datetime.fromisoformat(normalized)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts.astimezone(timezone.utc)
        except ValueError:
            return None

    def _determine_goal_text(self, state: ContextEngineState) -> str:
        """Extracts the current goal from the state."""
        if state.get("task_goal"):
            return str(state["task_goal"])
        messages = state.get("messages") or []
        if messages:
            last = messages[-1]
            if hasattr(last, "content"):
                try:
                    return "\n".join(last.content)
                except TypeError:
                    return str(last.content)
            return str(last)
        return ""

    def _embed_text(self, text: str) -> Dict[str, float]:
        """
        Creates a simple, normalized bag-of-words embedding for a string of 
        text.
        """
        if not text:
            return {}
        tokens = [token for token in re.findall(r"\b\w+\b", text.lower()) if len(token) > 2]
        if not tokens:
            return {}
        counts = Counter(tokens)
        norm = math.sqrt(sum(value * value for value in counts.values()))
        if norm == 0:
            return {}
        return {token: value / norm for token, value in counts.items()}

    def _cosine_similarity(
        self, lhs: Dict[str, float], rhs: Dict[str, float]
    ) -> float:
        """Calculates the cosine similarity between two bag-of-words embeddings."""
        if not lhs or not rhs:
            return 0.0
        keys = lhs.keys() & rhs.keys()
        if not keys:
            return 0.0
        value = sum(lhs[key] * rhs[key] for key in keys)
        return max(0.0, min(1.0, value))

"""Context quality scoring following the ACE framework."""

from __future__ import annotations

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Dict, Iterable, List

from ..config import ContextEngineConfig
from ..state import ContextEngineState, ContextSegment


@dataclass(slots=True)
class ScoreBreakdown:
    relevance: float
    coherence: float
    completeness: float
    freshness: float
    diversity: float
    efficiency: float


class ContextScorer:
    """Evaluates context quality using lightweight ACE-inspired heuristics."""

    def __init__(self, config: ContextEngineConfig) -> None:
        self.config = config

    async def evaluate(self, state: ContextEngineState) -> float:
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

    async def score_tool_output(self, tool_output: object) -> float:
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
        segments = list(segments)
        if not segments:
            return 1.0

        types = {segment.get("type", "").split(":", 1)[0] for segment in segments}
        diversity = len(types) / len(segments)
        return min(1.0, diversity)

    async def _score_efficiency(self, state: ContextEngineState) -> float:
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
        first = self._parse_timestamp(a)
        second = self._parse_timestamp(b)
        if not first or not second:
            return 0.5
        delta = abs((second - first).total_seconds())
        return min(1.0, delta / (3600 * 4))  # 4-hour horizon

    def _parse_timestamp(self, value: str | None) -> datetime | None:
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
        if not lhs or not rhs:
            return 0.0
        keys = lhs.keys() & rhs.keys()
        if not keys:
            return 0.0
        value = sum(lhs[key] * rhs[key] for key in keys)
        return max(0.0, min(1.0, value))

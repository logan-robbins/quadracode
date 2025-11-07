"""Long-term memory management for episodic and semantic knowledge."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence

from pydantic import BaseModel, Field

from .state import ExhaustionMode, QuadraCodeState, RefinementLedgerEntry
from .time_travel import get_time_travel_recorder
from .observability import get_meta_observer


class EpisodicMemoryRecord(BaseModel):
    cycle_id: str
    timestamp: datetime
    hypothesis: str
    status: str
    outcome_summary: str
    strategy: str | None = None
    exhaustion_trigger: str = ExhaustionMode.NONE.value
    dependencies: List[str] = Field(default_factory=list)
    tests: Dict[str, Any] = Field(default_factory=dict)
    causal_links: List[Dict[str, Any]] = Field(default_factory=list)
    telemetry: Dict[str, Any] = Field(default_factory=dict)


class SemanticMemoryPattern(BaseModel):
    pattern_id: str
    timestamp: datetime
    summary: str
    success_rate: float
    supporting_cycles: List[str] = Field(default_factory=list)
    risk_signals: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class MemoryGuidanceFrame(BaseModel):
    summary: str
    recommendations: List[str]
    supporting_cycles: List[str]
    risk_signals: List[str]
    last_refresh: datetime


def record_episode_from_ledger(
    state: QuadraCodeState,
    entry: RefinementLedgerEntry,
) -> EpisodicMemoryRecord:
    """Persist a completed refinement cycle as an episodic memory record."""

    episodes = state.setdefault("episodic_memory", [])
    record = EpisodicMemoryRecord(
        cycle_id=entry.cycle_id,
        timestamp=entry.timestamp,
        hypothesis=entry.hypothesis,
        status=entry.status,
        outcome_summary=entry.outcome_summary,
        strategy=entry.strategy,
        exhaustion_trigger=(entry.exhaustion_trigger.value if entry.exhaustion_trigger else ExhaustionMode.NONE.value),
        dependencies=list(entry.dependencies),
        tests=dict(entry.test_results or {}),
        causal_links=list(entry.causal_links or []),
        telemetry={
            "novelty_score": entry.novelty_score,
            "predicted_success_probability": entry.predicted_success_probability,
        },
    )
    episodes.append(record.model_dump(mode="json"))
    state["episodic_memory"] = episodes[-200:]

    get_time_travel_recorder().log_transition(
        state,
        event="episodic_memory_recorded",
        payload={"cycle_id": entry.cycle_id, "status": entry.status},
    )
    get_meta_observer().publish_ledger_event(
        "episodic_memory_recorded",
        {
            "cycle_id": entry.cycle_id,
            "status": entry.status,
            "strategy": entry.strategy,
        },
    )

    consolidate_memory(state)
    return record


def consolidate_memory(
    state: QuadraCodeState,
    *,
    window: int = 12,
) -> SemanticMemoryPattern | None:
    """Derive semantic memory patterns from recent episodes."""

    episodes = _hydrate_episodes(state)
    if len(episodes) < 3:
        return None

    recent = episodes[-window:]
    strategy_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"success": 0, "failure": 0, "cycles": [], "name": ""}
    )
    exhaustion_counter: Counter[str] = Counter()

    for episode in recent:
        strategy_key = (episode.strategy or "generic").lower()
        bucket = strategy_stats[strategy_key]
        bucket["name"] = strategy_key
        bucket["cycles"].append(episode.cycle_id)
        if episode.status.lower() == "succeeded":
            bucket["success"] += 1
        else:
            bucket["failure"] += 1
        if episode.exhaustion_trigger and episode.exhaustion_trigger != ExhaustionMode.NONE.value:
            exhaustion_counter[episode.exhaustion_trigger] += 1

    best_strategy = _select_best_strategy(strategy_stats.values())
    if not best_strategy:
        return None

    success = best_strategy["success"]
    total = success + best_strategy["failure"]
    if total == 0:
        return None

    success_rate = round(success / total, 4)
    pattern_id = f"pattern-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    risk_signals = _derive_risk_signals(exhaustion_counter)
    summary = (
        f"Strategy '{best_strategy['name']}' yields {success_rate:.0%} success over "
        f"{total} recent cycles. Maintain its guardrails and reuse supporting assets."
    )
    pattern = SemanticMemoryPattern(
        pattern_id=pattern_id,
        timestamp=datetime.now(timezone.utc),
        summary=summary,
        success_rate=success_rate,
        supporting_cycles=list(best_strategy["cycles"][-5:]),
        risk_signals=risk_signals,
        tags=[best_strategy["name"], "long_term_memory"],
    )

    patterns = state.setdefault("semantic_memory", [])
    patterns.append(pattern.model_dump(mode="json"))
    state["semantic_memory"] = patterns[-100:]

    log_entry = {
        "timestamp": pattern.timestamp.isoformat(timespec="seconds"),
        "pattern_id": pattern.pattern_id,
        "success_rate": pattern.success_rate,
    }
    consolidation_log = state.setdefault("memory_consolidation_log", [])
    consolidation_log.append(log_entry)
    state["memory_consolidation_log"] = consolidation_log[-200:]

    get_time_travel_recorder().log_transition(
        state,
        event="memory_consolidated",
        payload=pattern.model_dump(mode="json"),
    )
    get_meta_observer().publish_ledger_event(
        "memory_consolidated",
        {
            "pattern_id": pattern.pattern_id,
            "success_rate": pattern.success_rate,
        },
    )

    update_memory_guidance(state)
    return pattern


def update_memory_guidance(state: QuadraCodeState) -> Dict[str, Any]:
    """Refresh memory guidance hints used during hypothesis generation."""

    patterns = _hydrate_patterns(state)
    if not patterns:
        state["memory_guidance"] = {}
        return {}

    latest = patterns[-1]
    support = latest.supporting_cycles
    strategy_tag = latest.tags[0] if latest.tags else "documented strategy"
    recommendations = [
        f"Reuse strategy '{strategy_tag}' telemetry when drafting new hypotheses.",
        "Reference supporting cycles to avoid repeating failure handling steps.",
    ]
    if latest.risk_signals:
        recommendations.append(
            "Mitigate risk signals: " + ", ".join(latest.risk_signals[:3])
        )

    frame = MemoryGuidanceFrame(
        summary=latest.summary,
        recommendations=recommendations,
        supporting_cycles=support,
        risk_signals=latest.risk_signals,
        last_refresh=datetime.now(timezone.utc),
    )
    state["memory_guidance"] = frame.model_dump(mode="json")

    get_time_travel_recorder().log_transition(
        state,
        event="memory_guidance_updated",
        payload=state["memory_guidance"],
    )
    return state["memory_guidance"]


def _hydrate_episodes(state: QuadraCodeState) -> List[EpisodicMemoryRecord]:
    records: List[EpisodicMemoryRecord] = []
    for entry in state.get("episodic_memory", []):
        if isinstance(entry, EpisodicMemoryRecord):
            records.append(entry)
        elif isinstance(entry, dict):
            try:
                records.append(EpisodicMemoryRecord(**entry))
            except Exception:
                continue
    return records


def _hydrate_patterns(state: QuadraCodeState) -> List[SemanticMemoryPattern]:
    records: List[SemanticMemoryPattern] = []
    for entry in state.get("semantic_memory", []):
        if isinstance(entry, SemanticMemoryPattern):
            records.append(entry)
        elif isinstance(entry, dict):
            try:
                records.append(SemanticMemoryPattern(**entry))
            except Exception:
                continue
    return records


def _select_best_strategy(
    candidates: Iterable[Dict[str, Any]]
) -> Dict[str, Any] | None:
    best: Dict[str, Any] | None = None
    best_score = -math.inf
    for candidate in candidates:
        total = candidate["success"] + candidate["failure"]
        if total < 2:
            continue
        success_rate = candidate["success"] / total
        score = success_rate - (0.1 * candidate["failure"])
        if score > best_score:
            best_score = score
            candidate["name"] = candidate.get("name") or "strategy"
            best = candidate
    return best


def _derive_risk_signals(counter: Counter[str]) -> List[str]:
    if not counter:
        return []
    common = counter.most_common(3)
    return [f"{name}:{count}" for name, count in common]


__all__ = [
    "record_episode_from_ledger",
    "consolidate_memory",
    "update_memory_guidance",
]

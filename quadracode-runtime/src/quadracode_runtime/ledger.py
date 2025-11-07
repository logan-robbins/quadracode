"""Refinement ledger management utilities and tool handlers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Sequence, Tuple, Union

import networkx as nx
from langchain_core.messages import SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from .state import (
    ExhaustionMode,
    QuadraCodeState,
    RefinementLedgerEntry,
    add_refinement_ledger_entry,
)
from .long_term_memory import record_episode_from_ledger, update_memory_guidance

MANAGE_REFINEMENT_LEDGER_TOOL = "manage_refinement_ledger"

LedgerOperationLiteral = Literal[
    "propose_hypothesis",
    "conclude_hypothesis",
    "query_past_failures",
    "infer_causal_chain",
]

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class ManageLedgerPayload(BaseModel):
    operation: LedgerOperationLiteral
    hypothesis: str | None = None
    strategy: str | None = None
    summary: str | None = None
    status: str | None = None
    cycle_id: str | None = None
    dependencies: List[Union[str, int]] = Field(default_factory=list)
    filter: str | None = None
    limit: int | None = None
    include_tests: bool = False
    cycle_ids: List[Union[str, int]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class NoveltyAnalysis:
    score: float
    basis: List[str]
    blockers: List[str]
    similar_cycle: str | None = None


def process_manage_refinement_ledger_tool_response(
    state: QuadraCodeState,
    tool_response: Any,
) -> Tuple[QuadraCodeState, Dict[str, Any] | None]:
    """Handle manage_refinement_ledger tool invocations."""

    if not isinstance(tool_response, ToolMessage):
        return state, None

    tool_name = (tool_response.name or "").strip()
    if tool_name != MANAGE_REFINEMENT_LEDGER_TOOL:
        return state, None

    payload_dict = _parse_tool_message(tool_response)
    if not isinstance(payload_dict, dict):
        _append_system_message(
            state,
            "Refinement ledger request ignored: tool returned non-JSON payload.",
            metadata={"tool_call_id": tool_response.tool_call_id},
        )
        return state, None

    try:
        payload = ManageLedgerPayload(**payload_dict)
    except Exception as exc:  # pragma: no cover - defensive
        _append_system_message(
            state,
            f"Refinement ledger request rejected: {exc}",
            metadata={"tool_call_id": tool_response.tool_call_id},
        )
        return state, None

    operation = payload.operation
    if operation == "propose_hypothesis":
        return _handle_propose(state, payload, tool_response)
    if operation == "conclude_hypothesis":
        return _handle_conclude(state, payload, tool_response)
    if operation == "query_past_failures":
        return _handle_query_failures(state, payload, tool_response)
    if operation == "infer_causal_chain":
        return _handle_infer_causal_chain(state, payload, tool_response)

    return state, None


def _handle_propose(
    state: QuadraCodeState,
    payload: ManageLedgerPayload,
    tool_response: ToolMessage,
) -> Tuple[QuadraCodeState, Dict[str, Any] | None]:
    hypothesis = (payload.hypothesis or "").strip()
    if not hypothesis:
        return _reject_operation(
            state,
            "Hypothesis text is required for proposal",
            payload,
            tool_response,
        )

    strategy = (payload.strategy or "").strip() or None
    dependencies = _normalize_identifiers(payload.dependencies)
    novelty = _analyze_novelty(state, hypothesis, strategy, dependencies)
    if novelty.blockers:
        reason = "; ".join(novelty.blockers)
        return _reject_operation(state, reason, payload, tool_response)

    prediction = _predict_success_probability(state, hypothesis, strategy, novelty.score)
    cycle_id = (payload.cycle_id or "").strip() or _next_cycle_id(state)
    entry = RefinementLedgerEntry(
        cycle_id=cycle_id,
        timestamp=datetime.now(timezone.utc),
        hypothesis=hypothesis,
        status="proposed",
        outcome_summary=(payload.summary or "pending evaluation").strip(),
        strategy=strategy,
        novelty_score=novelty.score,
        novelty_basis=novelty.basis,
        dependencies=dependencies,
        predicted_success_probability=prediction,
        metadata=dict(payload.metadata or {}),
    )
    add_refinement_ledger_entry(state, entry)
    _record_metric(
        state,
        "refinement_ledger_proposed",
        {
            "cycle_id": cycle_id,
            "novelty_score": entry.novelty_score,
            "predicted_success_probability": entry.predicted_success_probability,
        },
    )
    message = (
        f"Recorded hypothesis {cycle_id} (novelty {entry.novelty_score:.2f}, "
        f"predicted success {entry.predicted_success_probability:.2f})."
    )
    _append_system_message(
        state,
        message,
        metadata={
            "operation": payload.operation,
            "cycle_id": cycle_id,
            "novelty_score": entry.novelty_score,
            "predicted_success_probability": entry.predicted_success_probability,
        },
    )
    return state, {
        "event": "refinement_ledger_proposed",
        "payload": {
            "cycle_id": cycle_id,
            "novelty_score": entry.novelty_score,
            "predicted_success_probability": entry.predicted_success_probability,
            "dependencies": dependencies,
            "strategy": strategy,
        },
    }


def _handle_conclude(
    state: QuadraCodeState,
    payload: ManageLedgerPayload,
    tool_response: ToolMessage,
) -> Tuple[QuadraCodeState, Dict[str, Any] | None]:
    cycle_id = (payload.cycle_id or "").strip()
    if not cycle_id:
        return _reject_operation(state, "cycle_id is required", payload, tool_response)

    ledger_entry = _find_entry(state, cycle_id)
    if ledger_entry is None:
        return _reject_operation(
            state,
            f"Cycle {cycle_id} does not exist",
            payload,
            tool_response,
        )

    status = (payload.status or "").strip() or "in_progress"
    summary = (payload.summary or "").strip() or "No summary provided."
    ledger_entry.status = status
    ledger_entry.outcome_summary = summary
    metadata = dict(payload.metadata or {})
    if metadata:
        ledger_entry.metadata.update(metadata)

    exhaustion_mode = state.get("exhaustion_mode", ExhaustionMode.NONE)
    if status.lower() == "failed":
        exhaustion_mode = ExhaustionMode.HYPOTHESIS_EXHAUSTED
    elif status.lower() == "succeeded" and exhaustion_mode == ExhaustionMode.HYPOTHESIS_EXHAUSTED:
        exhaustion_mode = ExhaustionMode.NONE
    state["exhaustion_mode"] = exhaustion_mode

    _append_system_message(
        state,
        f"Cycle {cycle_id} marked as {status}. Summary: {summary}",
        metadata={
            "operation": payload.operation,
            "cycle_id": cycle_id,
            "status": status,
        },
    )
    _record_metric(
        state,
        "refinement_ledger_concluded",
        {
            "cycle_id": cycle_id,
            "status": status,
        },
    )
    record_episode_from_ledger(state, ledger_entry)
    update_memory_guidance(state)
    return state, {
        "event": "refinement_ledger_concluded",
        "payload": {
            "cycle_id": cycle_id,
            "status": status,
        },
    }


def _handle_query_failures(
    state: QuadraCodeState,
    payload: ManageLedgerPayload,
    tool_response: ToolMessage,
) -> Tuple[QuadraCodeState, Dict[str, Any] | None]:
    limit = payload.limit or 5
    filter_text = (payload.filter or "").strip().lower()
    failures: List[RefinementLedgerEntry] = []
    for entry in state.get("refinement_ledger", []):
        if not isinstance(entry, RefinementLedgerEntry):
            continue
        if entry.status.lower() != "failed":
            continue
        if filter_text and filter_text not in entry.hypothesis.lower() and filter_text not in entry.outcome_summary.lower():
            continue
        failures.append(entry)

    failures.sort(key=lambda item: item.timestamp, reverse=True)
    results = [
        {
            "cycle_id": item.cycle_id,
            "hypothesis": item.hypothesis,
            "strategy": item.strategy,
            "summary": item.outcome_summary,
            "dependencies": list(item.dependencies),
            **({"tests": item.test_results} if payload.include_tests and item.test_results else {}),
        }
        for item in failures[:limit]
    ]

    summary_json = json.dumps(results, indent=2, default=str)
    _append_system_message(
        state,
        f"Queried {len(results)} failed hypotheses:\n{summary_json}",
        metadata={
            "operation": payload.operation,
            "count": len(results),
            "filter": filter_text or None,
        },
    )
    _record_metric(
        state,
        "refinement_ledger_query",
        {
            "count": len(results),
            "filter": filter_text or None,
        },
    )
    return state, {
        "event": "refinement_ledger_query",
        "payload": {
            "count": len(results),
            "filter": filter_text or None,
        },
    }


def _handle_infer_causal_chain(
    state: QuadraCodeState,
    payload: ManageLedgerPayload,
    tool_response: ToolMessage,
) -> Tuple[QuadraCodeState, Dict[str, Any] | None]:
    ledger_entries = [
        entry
        for entry in state.get("refinement_ledger", [])
        if isinstance(entry, RefinementLedgerEntry)
    ]
    graph = _build_dependency_graph(ledger_entries)
    targets = _normalize_identifiers(payload.cycle_ids) or [entry.cycle_id for entry in ledger_entries]
    nodes_to_process = [node for node in targets if graph.has_node(node)]
    insights: List[Dict[str, Any]] = []
    entry_lookup = {entry.cycle_id: entry for entry in ledger_entries}

    for target in nodes_to_process:
        predecessors = list(graph.predecessors(target))
        for source in predecessors:
            source_entry = entry_lookup.get(source)
            target_entry = entry_lookup.get(target)
            relationship = "influenced"
            confidence = 0.55
            if source_entry and source_entry.status.lower() == "failed":
                relationship = "blocked"
                confidence = 0.85
            elif source_entry and source_entry.status.lower() == "succeeded":
                relationship = "enabled"
                confidence = 0.72
            insight = {
                "source": source,
                "target": target,
                "relationship": relationship,
                "confidence": round(confidence, 2),
            }
            insights.append(insight)
            if target_entry:
                links = list(target_entry.causal_links or [])
                links.append(insight)
                target_entry.causal_links = links[-10:]

    if not insights:
        return _reject_operation(
            state,
            "No causal links could be inferred for the provided cycles.",
            payload,
            tool_response,
        )

    formatted = json.dumps(insights, indent=2)
    _append_system_message(
        state,
        f"Causal inference complete:\n{formatted}",
        metadata={
            "operation": payload.operation,
            "insight_count": len(insights),
        },
    )
    _record_metric(
        state,
        "refinement_ledger_causal_inference",
        {"insight_count": len(insights)},
    )
    return state, {
        "event": "refinement_ledger_causal_inference",
        "payload": {
            "insight_count": len(insights),
        },
    }


def _reject_operation(
    state: QuadraCodeState,
    reason: str,
    payload: ManageLedgerPayload,
    tool_response: ToolMessage,
) -> Tuple[QuadraCodeState, Dict[str, Any] | None]:
    _append_system_message(
        state,
        f"Refinement ledger request rejected: {reason}",
        metadata={
            "operation": payload.operation,
            "tool_call_id": tool_response.tool_call_id,
            "reason": reason,
        },
    )
    _record_metric(
        state,
        "refinement_ledger_rejected",
        {
            "operation": payload.operation,
            "reason": reason,
        },
    )
    return state, {
        "event": "refinement_ledger_rejected",
        "payload": {
            "operation": payload.operation,
            "reason": reason,
        },
    }


def _analyze_novelty(
    state: QuadraCodeState,
    hypothesis: str,
    strategy: str | None,
    dependencies: Sequence[str],
) -> NoveltyAnalysis:
    ledger_entries = [
        entry
        for entry in state.get("refinement_ledger", [])
        if isinstance(entry, RefinementLedgerEntry)
    ]
    tokens = _tokenize(hypothesis)
    max_similarity = 0.0
    similar_cycle: str | None = None
    basis: List[str] = []
    blockers: List[str] = []
    strategy_norm = (strategy or "").strip().lower()
    ledger_lookup = {entry.cycle_id: entry for entry in ledger_entries}

    for entry in ledger_entries:
        existing_tokens = _tokenize(entry.hypothesis)
        similarity = _token_similarity(tokens, existing_tokens)
        if similarity > max_similarity:
            max_similarity = similarity
            similar_cycle = entry.cycle_id
        if similarity >= 0.7:
            basis.append(
                f"Shares {similarity:.2f} similarity with {entry.cycle_id}"
            )
            entry_strategy = (entry.strategy or "").strip().lower()
            entry_status = entry.status.lower()
            if entry_status in {"failed", "abandoned"} and (
                not strategy_norm or strategy_norm == entry_strategy
            ):
                blockers.append(
                    f"Cycle {entry.cycle_id} previously {entry_status} without a new strategy"
                )

    for dependency in dependencies:
        dep_entry = ledger_lookup.get(dependency)
        if dep_entry and dep_entry.status.lower() != "succeeded":
            entry_strategy = (dep_entry.strategy or "").strip().lower()
            if not strategy_norm or strategy_norm == entry_strategy:
                blockers.append(
                    f"Dependency {dependency} is {dep_entry.status}; provide a new intervention strategy"
                )

    novelty_score = max(0.0, 1.0 - max_similarity)
    return NoveltyAnalysis(
        score=round(novelty_score, 4),
        basis=basis,
        blockers=blockers,
        similar_cycle=similar_cycle,
    )


def _predict_success_probability(
    state: QuadraCodeState,
    hypothesis: str,
    strategy: str | None,
    novelty_score: float,
) -> float:
    ledger_entries: List[RefinementLedgerEntry] = [
        entry
        for entry in state.get("refinement_ledger", [])
        if isinstance(entry, RefinementLedgerEntry)
    ]
    concluded = [
        entry
        for entry in ledger_entries
        if entry.status.lower() in {"succeeded", "failed"}
    ]
    if concluded:
        success_rate = sum(1 for entry in concluded if entry.status.lower() == "succeeded") / len(concluded)
    else:
        success_rate = 0.5

    tokens = _tokenize(hypothesis)
    similar_entries = [
        entry
        for entry in concluded
        if _token_similarity(tokens, _tokenize(entry.hypothesis)) >= 0.6
    ]
    if similar_entries:
        similar_success_rate = sum(1 for entry in similar_entries if entry.status.lower() == "succeeded") / len(similar_entries)
        combined = (similar_success_rate * 0.65) + (success_rate * 0.35)
    else:
        combined = success_rate

    novelty_multiplier = 0.4 + (0.6 * max(0.1, novelty_score))
    probability = min(max(combined * novelty_multiplier, 0.01), 0.99)
    return round(probability, 4)


def _build_dependency_graph(entries: Iterable[RefinementLedgerEntry]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for entry in entries:
        graph.add_node(entry.cycle_id, status=entry.status)
        for dependency in entry.dependencies:
            graph.add_edge(dependency, entry.cycle_id)
    return graph


def _find_entry(state: QuadraCodeState, cycle_id: str) -> RefinementLedgerEntry | None:
    for entry in state.get("refinement_ledger", []):
        if isinstance(entry, RefinementLedgerEntry) and entry.cycle_id == cycle_id:
            return entry
    return None


def _append_system_message(
    state: QuadraCodeState,
    content: str,
    *,
    metadata: Dict[str, Any] | None = None,
) -> None:
    message = SystemMessage(
        content=content,
        additional_kwargs={
            "source": "refinement_ledger",
            "metadata": metadata or {},
        },
    )
    messages = state.setdefault("messages", [])
    messages.append(message)


def _parse_tool_message(message: ToolMessage) -> Dict[str, Any] | None:
    content = message.content
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
        text = "".join(parts)
    else:
        text = str(content)

    text = text.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


def _token_similarity(lhs: set[str], rhs: set[str]) -> float:
    if not lhs or not rhs:
        return 0.0
    intersection = len(lhs & rhs)
    union = len(lhs | rhs)
    if union == 0:
        return 0.0
    return intersection / union


def _normalize_identifiers(values: Sequence[Union[str, int]]) -> List[str]:
    normalized: List[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized


def _next_cycle_id(state: QuadraCodeState) -> str:
    ledger = state.get("refinement_ledger", [])
    existing_ids = {
        entry.cycle_id
        for entry in ledger
        if isinstance(entry, RefinementLedgerEntry)
    }
    base = int(state.get("prp_cycle_count") or len(existing_ids) or 0) + 1
    while True:
        candidate = f"cycle-{base:04d}"
        if candidate not in existing_ids:
            return candidate
        base += 1


def _record_metric(state: QuadraCodeState, event: str, payload: Dict[str, Any]) -> None:
    metrics_log = state.setdefault("metrics_log", [])
    if isinstance(metrics_log, list):
        metrics_log.append({"event": event, "payload": payload})

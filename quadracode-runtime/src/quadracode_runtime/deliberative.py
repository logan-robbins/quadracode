"""Deliberative planning module for Systems-2 style reasoning chains."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any, Dict, Iterable, List, Sequence

import networkx as nx

from .state import ExhaustionMode, PRPState, QuadraCodeState, RefinementLedgerEntry


def _coerce_prp_state(value: Any) -> PRPState:
    if isinstance(value, PRPState):
        return value
    if isinstance(value, str):
        try:
            return PRPState(value)
        except ValueError:
            return PRPState.HYPOTHESIZE
    return PRPState.HYPOTHESIZE


def _coerce_exhaustion_mode(value: Any) -> ExhaustionMode:
    if isinstance(value, ExhaustionMode):
        return value
    if isinstance(value, str):
        try:
            return ExhaustionMode(value)
        except ValueError:
            return ExhaustionMode.NONE
    return ExhaustionMode.NONE


@dataclass(slots=True)
class ReasoningStep:
    step_id: str
    cycle_id: str
    phase: str
    goal: str
    hypothesis: str
    action: str
    expected_outcome: str
    confidence: float
    intermediate_state: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "cycle_id": self.cycle_id,
            "phase": self.phase,
            "goal": self.goal,
            "hypothesis": self.hypothesis,
            "action": self.action,
            "expected_outcome": self.expected_outcome,
            "confidence": round(self.confidence, 4),
            "intermediate_state": dict(self.intermediate_state),
        }


@dataclass(slots=True)
class CounterfactualScenario:
    scenario_id: str
    pivot_cycle: str | None
    proposition: str
    intervention: str
    projected_outcome: str
    likelihood: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "pivot_cycle": self.pivot_cycle,
            "proposition": self.proposition,
            "intervention": self.intervention,
            "projected_outcome": self.projected_outcome,
            "likelihood": round(self.likelihood, 4),
        }


@dataclass(slots=True)
class CausalGraphSnapshot:
    nodes: int
    edges: int
    bottlenecks: List[str]
    accelerants: List[str]
    insights: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "bottlenecks": list(self.bottlenecks),
            "accelerants": list(self.accelerants),
            "insights": [dict(item) for item in self.insights],
        }


@dataclass(slots=True)
class ProbabilisticProjection:
    success_probability: float
    uncertainty: float
    risk_factors: List[str]
    supporting_cycles: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success_probability": round(self.success_probability, 4),
            "uncertainty": round(self.uncertainty, 4),
            "risk_factors": list(self.risk_factors),
            "supporting_cycles": list(self.supporting_cycles),
        }


@dataclass(slots=True)
class DeliberativePlanArtifacts:
    reasoning_chain: List[ReasoningStep]
    intermediate_states: List[Dict[str, Any]]
    counterfactuals: List[CounterfactualScenario]
    causal_graph: CausalGraphSnapshot
    probabilistic_projection: ProbabilisticProjection
    synopsis: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reasoning_chain": [step.to_dict() for step in self.reasoning_chain],
            "intermediate_states": [dict(item) for item in self.intermediate_states],
            "counterfactuals": [scenario.to_dict() for scenario in self.counterfactuals],
            "causal_graph": self.causal_graph.to_dict(),
            "probabilistic_projection": self.probabilistic_projection.to_dict(),
            "synopsis": self.synopsis,
        }


class DeliberativePlanner:
    """Systems-2 planner composing reasoning, counterfactual, and causal views."""

    def __init__(
        self,
        *,
        max_reasoning_steps: int = 5,
        max_counterfactuals: int = 3,
    ) -> None:
        self.max_reasoning_steps = max(1, max_reasoning_steps)
        self.max_counterfactuals = max(1, max_counterfactuals)

    def build_plan(self, state: QuadraCodeState) -> DeliberativePlanArtifacts:
        ledger_entries = self._hydrate_ledger(state)
        reasoning_chain = self._build_reasoning_chain(state, ledger_entries)
        intermediate_states = [step.intermediate_state for step in reasoning_chain]
        counterfactuals = self._generate_counterfactuals(state, ledger_entries)
        causal_graph = self._infer_causal_graph(ledger_entries)
        projection = self._estimate_probabilistic_projection(
            state,
            reasoning_chain,
            counterfactuals,
        )
        synopsis = self._compose_synopsis(
            reasoning_chain,
            counterfactuals,
            causal_graph,
            projection,
        )
        return DeliberativePlanArtifacts(
            reasoning_chain=reasoning_chain,
            intermediate_states=intermediate_states,
            counterfactuals=counterfactuals,
            causal_graph=causal_graph,
            probabilistic_projection=projection,
            synopsis=synopsis,
        )

    def _hydrate_ledger(
        self, state: QuadraCodeState
    ) -> List[RefinementLedgerEntry]:
        entries: List[RefinementLedgerEntry] = []
        raw_entries = state.get("refinement_ledger", [])
        for entry in raw_entries:
            if isinstance(entry, RefinementLedgerEntry):
                entries.append(entry)
                continue
            if isinstance(entry, dict):
                try:
                    entries.append(RefinementLedgerEntry(**entry))
                except Exception:
                    continue
        entries.sort(key=lambda item: item.timestamp, reverse=True)
        return entries

    def _build_reasoning_chain(
        self,
        state: QuadraCodeState,
        ledger_entries: Sequence[RefinementLedgerEntry],
    ) -> List[ReasoningStep]:
        goal = str(state.get("task_goal") or "Advance the active objective")
        prp_state = _coerce_prp_state(state.get("prp_state"))
        exhaustion_mode = _coerce_exhaustion_mode(state.get("exhaustion_mode"))
        context_quality = float(state.get("context_quality_score", 0.0) or 0.0)
        context_tokens = int(state.get("context_window_used", 0) or 0)
        steps: List[ReasoningStep] = []
        window = list(ledger_entries[: self.max_reasoning_steps])

        if not window:
            steps.append(
                ReasoningStep(
                    step_id="step-1",
                    cycle_id="cycle-1",
                    phase=prp_state.value,
                    goal=goal,
                    hypothesis="Seed a concrete hypothesis from the task brief.",
                    action="Draft hypothesis and execution plan",
                    expected_outcome="Establish measurable acceptance criteria.",
                    confidence=0.5,
                    intermediate_state={
                        "cycle_id": "cycle-1",
                        "phase": prp_state.value,
                        "prp_state": prp_state.value,
                        "exhaustion_mode": exhaustion_mode.value,
                        "context_quality": round(context_quality, 3),
                        "context_tokens": context_tokens,
                        "dependencies": [],
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "status": "pending",
                    },
                )
            )
            return steps

        for index, entry in enumerate(window, start=1):
            phase = self._resolve_phase(entry)
            action = (entry.strategy or "Refine implementation").strip()
            if not action:
                action = "Refine implementation"
            intermediate_state = {
                "cycle_id": entry.cycle_id,
                "phase": phase,
                "prp_state": prp_state.value,
                "exhaustion_mode": exhaustion_mode.value,
                "context_quality": round(context_quality, 3),
                "context_tokens": context_tokens,
                "dependencies": list(entry.dependencies),
                "timestamp": entry.timestamp.isoformat(),
                "status": entry.status,
                "novelty_score": entry.novelty_score,
            }
            steps.append(
                ReasoningStep(
                    step_id=f"step-{index}",
                    cycle_id=entry.cycle_id,
                    phase=phase,
                    goal=goal,
                    hypothesis=entry.hypothesis,
                    action=action,
                    expected_outcome=entry.outcome_summary,
                    confidence=float(entry.predicted_success_probability or 0.5),
                    intermediate_state=intermediate_state,
                )
            )
        return steps

    def _resolve_phase(self, entry: RefinementLedgerEntry) -> str:
        status = entry.status.lower()
        if status in {"proposed", "draft", "ideation"}:
            return PRPState.HYPOTHESIZE.value
        if status in {"in_progress", "executing"}:
            return PRPState.EXECUTE.value
        if status in {"testing", "validation"}:
            return PRPState.TEST.value
        if status in {"succeeded", "completed"}:
            return PRPState.CONCLUDE.value
        if status in {"failed", "abandoned"}:
            return PRPState.HYPOTHESIZE.value
        if entry.test_results:
            return PRPState.TEST.value
        return PRPState.EXECUTE.value

    def _generate_counterfactuals(
        self,
        state: QuadraCodeState,
        ledger_entries: Sequence[RefinementLedgerEntry],
    ) -> List[CounterfactualScenario]:
        failures = [entry for entry in ledger_entries if entry.status.lower() != "succeeded"]
        if not failures:
            failures = list(ledger_entries[: self.max_counterfactuals])
        scenarios: List[CounterfactualScenario] = []
        for index, entry in enumerate(failures[: self.max_counterfactuals], start=1):
            likelihood = 1.0 - float(entry.predicted_success_probability or 0.45)
            likelihood = max(0.15, min(0.9, likelihood))
            intervention = self._suggest_intervention(entry)
            dependent_summary = ", ".join(entry.dependencies[:3]) if entry.dependencies else "broader system"
            projected = (
                f"Unlock downstream work on {dependent_summary} by addressing {entry.hypothesis[:80]}"
            )
            scenarios.append(
                CounterfactualScenario(
                    scenario_id=f"cf-{index}",
                    pivot_cycle=entry.cycle_id,
                    proposition=f"If {entry.cycle_id} pivoted: {entry.hypothesis[:120]}",
                    intervention=intervention,
                    projected_outcome=projected,
                    likelihood=round(likelihood, 4),
                )
            )
        return scenarios

    def _suggest_intervention(self, entry: RefinementLedgerEntry) -> str:
        status = entry.status.lower()
        if status == "failed":
            return "Introduce debugger agent and rerun property tests"
        if status == "abandoned":
            return "Reframe hypothesis with new acceptance criteria"
        if status == "succeeded":
            return "Propagate learnings to dependent cycles"
        if entry.exhaustion_trigger == ExhaustionMode.TEST_FAILURE:
            return "Fix failing tests before execution"
        if entry.exhaustion_trigger == ExhaustionMode.TOOL_BACKPRESSURE:
            return "Split tool usage across specialized agents"
        return "Strengthen evidence via targeted instrumentation"

    def _infer_causal_graph(
        self, ledger_entries: Sequence[RefinementLedgerEntry]
    ) -> CausalGraphSnapshot:
        graph = nx.DiGraph()
        status_map: Dict[str, str] = {}
        for entry in ledger_entries:
            graph.add_node(entry.cycle_id)
            status_map[entry.cycle_id] = entry.status.lower()
            for dependency in entry.dependencies:
                graph.add_edge(dependency, entry.cycle_id)

        if graph.number_of_nodes() == 0:
            return CausalGraphSnapshot(0, 0, [], [], [])

        insights: List[Dict[str, Any]] = []
        for source, target in graph.edges:
            relationship = "influences"
            confidence = 0.6
            source_status = status_map.get(source, "unknown")
            if source_status == "failed":
                relationship = "blocks"
                confidence = 0.82
            elif source_status == "succeeded":
                relationship = "enables"
                confidence = 0.71
            insights.append(
                {
                    "source": source,
                    "target": target,
                    "relationship": relationship,
                    "confidence": round(confidence, 2),
                }
            )

        bottlenecks = sorted(
            node
            for node in graph.nodes
            if graph.out_degree(node) >= 2 and status_map.get(node) == "failed"
        )
        accelerants = sorted(
            node
            for node in graph.nodes
            if graph.out_degree(node) >= 1 and status_map.get(node) == "succeeded"
        )

        return CausalGraphSnapshot(
            nodes=graph.number_of_nodes(),
            edges=graph.number_of_edges(),
            bottlenecks=bottlenecks,
            accelerants=accelerants,
            insights=insights,
        )

    def _estimate_probabilistic_projection(
        self,
        state: QuadraCodeState,
        reasoning_chain: Sequence[ReasoningStep],
        counterfactuals: Sequence[CounterfactualScenario],
    ) -> ProbabilisticProjection:
        confidences = [step.confidence for step in reasoning_chain if step.confidence > 0]
        if not confidences:
            confidences = [0.5]
        mean_confidence = fmean(confidences)
        variance = pstdev(confidences) if len(confidences) > 1 else 0.08
        exhaustion_mode = _coerce_exhaustion_mode(state.get("exhaustion_mode"))
        penalty_map = {
            ExhaustionMode.TEST_FAILURE: 0.3,
            ExhaustionMode.TOOL_BACKPRESSURE: 0.2,
            ExhaustionMode.RETRY_DEPLETION: 0.2,
            ExhaustionMode.CONTEXT_SATURATION: 0.1,
            ExhaustionMode.LLM_STOP: 0.25,
            ExhaustionMode.HYPOTHESIS_EXHAUSTED: 0.15,
            ExhaustionMode.PREDICTED_EXHAUSTION: 0.1,
        }
        penalty = penalty_map.get(exhaustion_mode, 0.0)
        adjusted = max(0.01, min(0.99, mean_confidence * (1 - penalty)))
        uncertainty = min(0.95, variance + 0.15 + penalty + (0.05 * len(counterfactuals)))
        invariants = state.get("invariants") if isinstance(state.get("invariants"), dict) else {}
        risk_factors: List[str] = []
        if exhaustion_mode is not ExhaustionMode.NONE:
            risk_factors.append(f"Exhaustion mode active: {exhaustion_mode.value}")
        if len(counterfactuals) >= 2:
            risk_factors.append("Multiple counterfactual branches queued")
        if invariants.get("needs_test_after_rejection"):
            risk_factors.append("Tests pending after rejection")
        if not invariants.get("skepticism_gate_satisfied", False):
            risk_factors.append("Skepticism gate not satisfied in current cycle")

        supporting_cycles = [step.cycle_id for step in reasoning_chain]
        return ProbabilisticProjection(
            success_probability=round(adjusted, 4),
            uncertainty=round(uncertainty, 4),
            risk_factors=risk_factors,
            supporting_cycles=supporting_cycles,
        )

    def _compose_synopsis(
        self,
        reasoning_chain: Sequence[ReasoningStep],
        counterfactuals: Sequence[CounterfactualScenario],
        causal_graph: CausalGraphSnapshot,
        projection: ProbabilisticProjection,
    ) -> str:
        steps_summary = ", ".join(
            f"{step.step_id}:{step.phase}" for step in reasoning_chain
        )
        bottleneck_summary = ", ".join(causal_graph.bottlenecks) or "none"
        counterfactual_summary = ", ".join(
            scenario.scenario_id for scenario in counterfactuals
        ) or "none"
        return (
            f"Reasoning chain [{steps_summary}] with counterfactuals {counterfactual_summary}. "
            f"Bottlenecks: {bottleneck_summary}. "
            f"Projected success {projection.success_probability:.2f} ± {projection.uncertainty:.2f}."
        )

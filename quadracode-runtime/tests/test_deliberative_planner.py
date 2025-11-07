from datetime import datetime, timezone

from quadracode_runtime.deliberative import DeliberativePlanner
from quadracode_runtime.state import (
    ExhaustionMode,
    PRPState,
    RefinementLedgerEntry,
    make_initial_context_engine_state,
)


def _ledger_entry(
    cycle_id: str,
    *,
    status: str,
    hypothesis: str,
    outcome: str,
    dependencies: list[str] | None = None,
    strategy: str | None = None,
    predicted_success: float | None = None,
) -> RefinementLedgerEntry:
    return RefinementLedgerEntry(
        cycle_id=cycle_id,
        timestamp=datetime.now(timezone.utc),
        hypothesis=hypothesis,
        status=status,
        outcome_summary=outcome,
        exhaustion_trigger=ExhaustionMode.TEST_FAILURE if status == "failed" else ExhaustionMode.NONE,
        test_results={"overall_status": "failed" if status == "failed" else "passed"},
        strategy=strategy,
        novelty_score=0.42,
        novelty_basis=["token overlap"],
        dependencies=dependencies or [],
        predicted_success_probability=predicted_success,
        metadata={"author": "planner"},
    )


def test_deliberative_planner_builds_reasoning_chain_from_ledger() -> None:
    planner = DeliberativePlanner(max_reasoning_steps=3, max_counterfactuals=2)
    state = make_initial_context_engine_state(context_window_max=32_000)
    state["task_goal"] = "Ship deliberative planner"
    state["prp_state"] = PRPState.EXECUTE
    state["context_quality_score"] = 0.81
    state["context_window_used"] = 2048
    state["exhaustion_mode"] = ExhaustionMode.NONE
    state["refinement_ledger"] = [
        _ledger_entry(
            "cycle-3",
            status="succeeded",
            hypothesis="Document planner",
            outcome="Docs published",
            dependencies=["cycle-1"],
            strategy="documentation",
            predicted_success=0.78,
        ),
        _ledger_entry(
            "cycle-2",
            status="failed",
            hypothesis="Write planner core",
            outcome="Tests blocked",
            dependencies=["cycle-1"],
            strategy="implementation",
            predicted_success=0.55,
        ),
    ]

    plan = planner.build_plan(state)

    assert len(plan.reasoning_chain) == 2
    chain_ids = [step.cycle_id for step in plan.reasoning_chain]
    assert chain_ids[0] == "cycle-2"
    assert set(chain_ids) == {"cycle-2", "cycle-3"}
    assert plan.counterfactuals, "Counterfactual scenarios should be generated"
    assert plan.causal_graph.nodes >= 2
    assert 0 < plan.probabilistic_projection.success_probability <= 1
    assert plan.synopsis.startswith("Reasoning chain"), plan.synopsis
    serialized = plan.to_dict()
    assert serialized["reasoning_chain"][0]["step_id"] == "step-1"


def test_deliberative_planner_handles_empty_ledger() -> None:
    planner = DeliberativePlanner()
    state = make_initial_context_engine_state(context_window_max=16_000)
    state["task_goal"] = "Propose first hypothesis"
    state["refinement_ledger"] = []

    plan = planner.build_plan(state)

    assert len(plan.reasoning_chain) == 1
    assert plan.reasoning_chain[0].cycle_id == "cycle-1"
    assert plan.counterfactuals == []
    assert plan.causal_graph.nodes == 0

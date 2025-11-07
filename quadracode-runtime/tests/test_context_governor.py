from datetime import datetime, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import (
    ContextSegment,
    ExhaustionMode,
    PRPState,
    RefinementLedgerEntry,
    make_initial_context_engine_state,
)


def _make_segment(segment_id: str, *, content: str, priority: int, segment_type: str = "conversation") -> ContextSegment:
    tokens = len(content.split())
    return {
        "id": segment_id,
        "content": content,
        "type": segment_type,
        "priority": priority,
        "token_count": tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }


def _ledger_entry(
    cycle_id: str,
    *,
    status: str,
    hypothesis: str,
    outcome: str,
    dependencies: list[str] | None = None,
) -> RefinementLedgerEntry:
    return RefinementLedgerEntry(
        cycle_id=cycle_id,
        timestamp=datetime.now(timezone.utc),
        hypothesis=hypothesis,
        status=status,
        outcome_summary=outcome,
        exhaustion_trigger=ExhaustionMode.NONE,
        predicted_success_probability=0.6,
        dependencies=dependencies or [],
        test_results={"overall_status": "passed" if status == "succeeded" else "failed"},
        novelty_score=0.5,
        novelty_basis=["unit-test"],
        strategy="integration",
    )


def test_governor_plan_override_applies_actions_and_updates_outline() -> None:
    config = ContextEngineConfig(
        metrics_enabled=False,
        reducer_model="heuristic",
        governor_model=None,
        context_window_max=800,
        target_context_size=600,
    )
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [
        _make_segment("s1", content="alpha beta gamma", priority=9),
        _make_segment("s2", content="error stack trace line one\nline two\nline three", priority=7),
        _make_segment("s3", content="obsolete background information", priority=3),
        _make_segment("s4", content="very long historical context " + "detail " * 80, priority=5),
    ]
    state["context_window_used"] = sum(seg["token_count"] for seg in state["context_segments"])
    initial_s2_tokens = state["context_segments"][1]["token_count"]

    plan = {
        "actions": [
            {"segment_id": "s1", "decision": "retain", "priority": 8},
            {"segment_id": "s2", "decision": "summarize", "focus": "errors"},
            {"segment_id": "s3", "decision": "discard"},
            {"segment_id": "s4", "decision": "externalize"},
        ],
        "prompt_outline": {
            "system": "Prioritize resolving current errors before new work.",
            "focus": ["error summary", "recent decisions"],
            "ordered_segments": ["s2", "s1"],
        },
    }
    state["governor_plan_override"] = plan

    result = engine.govern_context_sync(state)

    segment_ids = [segment["id"] for segment in result["context_segments"]]
    assert "s3" not in segment_ids
    assert segment_ids[:2] == ["s2", "s1"], "ordered_segments should lead context ordering"

    s1 = next(segment for segment in result["context_segments"] if segment["id"] == "s1")
    s2 = next(segment for segment in result["context_segments"] if segment["id"] == "s2")
    s4 = next(segment for segment in result["context_segments"] if segment["id"] == "s4")

    assert s1["priority"] == 8
    assert s2["type"].startswith("summary:"), "summarized segments should be marked"
    assert s2["token_count"] <= initial_s2_tokens
    assert s4["type"].startswith("pointer:"), "externalized segments should become pointers"
    assert s4["restorable_reference"] is not None

    assert result["governor_plan"] == plan
    assert result["governor_prompt_outline"] == plan["prompt_outline"]
    assert result["metrics_log"], "governor plan should emit metrics"
    events = [entry["event"] for entry in result["metrics_log"]]
    assert "governor_plan" in events
    assert "externalize" in events

    used_tokens = result["context_window_used"]
    assert used_tokens == sum(segment["token_count"] for segment in result["context_segments"])


def test_govern_context_populates_deliberative_plan_fields() -> None:
    config = ContextEngineConfig(
        metrics_enabled=False,
        reducer_model="heuristic",
        governor_model=None,
        context_window_max=1024,
    )
    engine = ContextEngine(config)
    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["task_goal"] = "Deliver deliberate planning module"
    state["prp_state"] = PRPState.EXECUTE
    state["context_segments"] = [
        _make_segment("primary", content="Focus on planner", priority=9),
        _make_segment("aux", content="Secondary context", priority=4),
    ]
    state["context_window_used"] = sum(seg["token_count"] for seg in state["context_segments"])
    state["semantic_memory"] = [
        {
            "pattern_id": "pattern-test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": "Test pattern",
            "success_rate": 0.8,
            "supporting_cycles": ["cycle-1"],
            "risk_signals": ["llm_stop:1"],
            "tags": ["planner"],
        }
    ]
    state["refinement_ledger"] = [
        _ledger_entry(
            "cycle-5",
            status="succeeded",
            hypothesis="Integrate planner outputs",
            outcome="Plan stored in state",
            dependencies=["cycle-3"],
        ),
        _ledger_entry(
            "cycle-4",
            status="failed",
            hypothesis="Prototype planner",
            outcome="Tests failed",
            dependencies=["cycle-2"],
        ),
    ]

    result = engine.govern_context_sync(state)

    plan = result.get("deliberative_plan")
    assert isinstance(plan, dict) and plan.get("reasoning_chain"), "Deliberative plan should be recorded"
    assert result.get("deliberative_synopsis"), "Synopsis should be present"
    assert isinstance(result.get("counterfactual_register"), list)
    assert isinstance(result.get("causal_graph_snapshot"), dict)
    assert 0 <= result.get("planning_success_probability", 0.0) <= 1
    assert result.get("planning_uncertainty", 0.0) > 0
    guidance = result.get("memory_guidance")
    assert isinstance(guidance, dict) and guidance.get("summary"), "Memory guidance should be injected"

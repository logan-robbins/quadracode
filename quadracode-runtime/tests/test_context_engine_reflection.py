import asyncio
from datetime import datetime, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import make_initial_context_engine_state


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_post_process_updates_playbook_and_rules() -> None:
    config = ContextEngineConfig(metrics_enabled=False, reducer_model="heuristic")
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["messages"] = []
    state["context_segments"] = [
        {
            "id": "s1",
            "content": "recent code snippet",
            "type": "tool_outputs",
            "priority": 5,
            "token_count": 300,
            "timestamp": _now_iso(),
            "decay_rate": 0.1,
            "compression_eligible": True,
            "restorable_reference": None,
        }
    ]
    state["context_window_used"] = 400
    state["context_quality_score"] = 0.55
    state["context_quality_components"] = {
        "relevance": 0.45,
        "coherence": 0.62,
        "completeness": 0.30,
        "freshness": 0.50,
        "diversity": 0.40,
        "efficiency": 0.70,
    }

    result_state = asyncio.run(engine.post_process(state))

    assert result_state["reflection_log"], "reflection log should be populated"
    last_reflection = result_state["reflection_log"][-1]
    assert last_reflection["issues"], "reflection should capture issues"
    assert any("completeness" in issue for issue in last_reflection["issues"])

    playbook = result_state["context_playbook"]
    assert playbook.get("iterations") == 1
    assert playbook.get("last_reflection")
    assert playbook["last_reflection"]["focus_metric"] in {"relevance", "completeness", "diversity"}

    # Curation rules get appended when a focus metric exists
    assert result_state["curation_rules"], "curation rules should grow after reflection"
    rule = result_state["curation_rules"][-1]
    assert "actions" in rule and rule["actions"], "rule should contain remediation actions"

    assert result_state["metrics_log"], "metrics events should be recorded"


def test_reflection_deduplicates_recommendations() -> None:
    config = ContextEngineConfig(metrics_enabled=False, reducer_model="heuristic")
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = []
    state["context_quality_score"] = 0.3
    state["context_quality_components"] = {
        "relevance": 0.2,
        "coherence": 0.2,
        "completeness": 0.2,
        "freshness": 0.9,
        "diversity": 0.9,
        "efficiency": 0.9,
    }

    reflected = asyncio.run(engine.post_process(state))

    last = reflected["reflection_log"][-1]
    recs = last["recommendations"]
    assert len(recs) == len(set(recs)), "recommendations should be deduplicated"


def test_metrics_emit_for_tool_responses() -> None:
    config = ContextEngineConfig(metrics_enabled=False, reducer_model="heuristic")
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = []

    # Run through pre -> tool -> post to ensure metrics log grows
    state = asyncio.run(engine.pre_process(state))
    assert state["metrics_log"], "pre_process should log metrics"

    state = asyncio.run(engine.handle_tool_response(state, {"result": "ok"}))
    assert state["metrics_log"][-1]["event"] == "tool_response"

    state = asyncio.run(engine.post_process(state))
    assert state["metrics_log"][-1]["event"] == "post_process"

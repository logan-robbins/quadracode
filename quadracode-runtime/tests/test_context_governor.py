from datetime import datetime, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import ContextSegment, make_initial_context_engine_state


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

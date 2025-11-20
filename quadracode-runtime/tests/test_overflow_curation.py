import asyncio

from datetime import datetime, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import make_initial_context_engine_state


def _make_segment(segment_id: str, priority: int, tokens: int):
    return {
        "id": segment_id,
        "content": "token " * tokens,
        "type": "conversation",
        "priority": priority,
        "token_count": tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }


def test_overflow_triggers_curation_even_with_high_quality() -> None:
    config = ContextEngineConfig(
        metrics_enabled=False,
        context_window_max=500,
        optimal_context_size=400,
    )
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [
        _make_segment("s1", priority=9, tokens=250),
        _make_segment("s2", priority=8, tokens=240),
        _make_segment("s3", priority=1, tokens=180),
    ]
    state["context_window_used"] = sum(seg["token_count"] for seg in state["context_segments"])
    state["context_quality_score"] = 0.95
    state["context_quality_components"] = {
        "relevance": 0.92,
        "coherence": 0.91,
        "completeness": 0.93,
        "freshness": 0.90,
        "diversity": 0.94,
        "efficiency": 0.89,
    }

    result = asyncio.run(engine.pre_process(state))

    total_tokens = sum(segment["token_count"] for segment in result["context_segments"])
    assert total_tokens <= config.optimal_context_size
    assert result["context_window_used"] == total_tokens
    assert any(seg["type"].startswith("pointer:") for seg in result["context_segments"])

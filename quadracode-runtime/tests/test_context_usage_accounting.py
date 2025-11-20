from datetime import datetime, timezone

from langchain_core.messages import ToolMessage

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import ContextSegment, make_initial_context_engine_state


def _make_segment(segment_id: str, tokens: int) -> ContextSegment:
    return {
        "id": segment_id,
        "content": "word " * tokens,
        "type": "conversation",
        "priority": 5,
        "token_count": tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }


def test_pre_process_recomputes_usage_after_curation_and_loader() -> None:
    config = ContextEngineConfig(metrics_enabled=False)
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [
        _make_segment("s1", 120),
        _make_segment("s2", 80),
    ]
    state["context_window_used"] = 0

    result = engine.pre_process_sync(state)

    total_tokens = sum(segment["token_count"] for segment in result["context_segments"])
    assert result["context_window_used"] == total_tokens


def test_tool_response_updates_context_window_used() -> None:
    config = ContextEngineConfig(metrics_enabled=False)
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["messages"] = [
        ToolMessage(
            content="alpha beta gamma delta",
            name="diagnostics",
            tool_call_id="diag-1",
        )
    ]
    state["context_window_used"] = 0

    result = engine.handle_tool_response_sync(state)

    total_tokens = sum(segment["token_count"] for segment in result["context_segments"])
    assert result["context_window_used"] == total_tokens

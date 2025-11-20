import asyncio
from datetime import datetime, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.nodes.context_operations import ContextOperation
from quadracode_runtime.state import make_initial_context_engine_state


def _make_segment(segment_id: str, *, tokens: int, priority: int) -> dict:
    return {
        "id": segment_id,
        "content": "sentence " * max(tokens, 1),
        "type": "conversation",
        "priority": priority,
        "token_count": tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }


def _make_state(config: ContextEngineConfig):
    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["messages"] = []
    return state


def _make_message(text: str):
    class DummyMessage:
        def __init__(self, value: str) -> None:
            self.content = [value]

    return DummyMessage(text)


def test_pre_process_compresses_overflow_context() -> None:
    config = ContextEngineConfig(
        context_window_max=2000,
        optimal_context_size=1500,
        quality_threshold=0.8,
        metrics_enabled=False,
    )
    engine = ContextEngine(config)

    state = _make_state(config)
    segments = [
        _make_segment("seg-high-1", tokens=900, priority=9),
        _make_segment("seg-high-2", tokens=800, priority=8),
        _make_segment("seg-low-1", tokens=700, priority=3),
        _make_segment("seg-low-2", tokens=600, priority=2),
    ]
    state["context_segments"] = segments
    state["context_window_used"] = sum(seg["token_count"] for seg in segments)

    result = asyncio.run(engine.pre_process(state))

    compressed_total = sum(seg["token_count"] for seg in result["context_segments"])
    assert compressed_total <= config.optimal_context_size
    assert result["context_window_used"] <= config.context_window_max

    pointer_segments = [seg for seg in result["context_segments"] if seg["type"].startswith("pointer:")]
    assert pointer_segments, "Expected some segments to be externalized"


def test_tool_response_truncates_large_payloads() -> None:
    config = ContextEngineConfig(metrics_enabled=False, max_tool_payload_chars=1200)
    engine = ContextEngine(config)

    state = _make_state(config)
    large_text = "line " * 1024

    result = asyncio.run(engine.handle_tool_response(state, {"output": large_text}))

    last_segment = result["context_segments"][-1]
    assert last_segment["type"] == "tool_output"
    assert len(last_segment["content"]) <= 4100  # includes ellipsis
    assert last_segment.get("restorable_reference") == last_segment["id"]


def test_prefetch_queue_tracks_unloaded_needs() -> None:
    config = ContextEngineConfig(context_window_max=1000, metrics_enabled=False)
    engine = ContextEngine(config)

    state = _make_state(config)
    state["context_window_used"] = 950
    state["messages"] = [_make_message("Please implement the code loader")]

    result = asyncio.run(engine.pre_process(state))

    assert any(entry["type"] in {"code_context", "file_structure"} for entry in result["prefetch_queue"])


def test_summarize_operation_invokes_reducer() -> None:
    config = ContextEngineConfig(
        metrics_enabled=False,
        reducer_target_tokens=40,
        max_tool_payload_chars=2000,
    )
    engine = ContextEngine(config)

    state = _make_state(config)
    large_content = " ".join(["detail"] * 1200)

    result = asyncio.run(
        engine._apply_operation(state, large_content, ContextOperation.SUMMARIZE)
    )

    last = result["context_segments"][-1]
    assert last["token_count"] <= config.reducer_target_tokens + 10

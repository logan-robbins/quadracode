from datetime import datetime, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import make_initial_context_engine_state


def _make_segment(segment_id: str, *, tokens: int, priority: int, segment_type: str = "conversation") -> dict:
    return {
        "id": segment_id,
        "content": "detail " * tokens,
        "type": segment_type,
        "priority": priority,
        "token_count": tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }


def test_pre_process_emits_curation_and_load_metrics() -> None:
    config = ContextEngineConfig(
        metrics_enabled=False,
        quality_threshold=0.99,
        context_window_max=5000,
        target_context_size=160,
    )
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [
        _make_segment("seg-high", tokens=400, priority=8, segment_type="memory"),
        _make_segment("seg-low", tokens=60, priority=2),
    ]

    class DummyMessage:
        def __init__(self, text: str) -> None:
            self.content = text

    state["messages"] = [DummyMessage("Please implement the API and add tests")]

    result = engine.pre_process_sync(state)

    events = [entry["event"] for entry in result["metrics_log"]]
    assert "curation" in events
    assert "load" in events
    assert "externalize" in events

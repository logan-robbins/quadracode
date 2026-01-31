import asyncio
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import make_initial_context_engine_state


def _make_segment(segment_id: str, tokens: int) -> dict:
    return {
        "id": segment_id,
        "content": "artifact " * max(tokens, 1),
        "type": "tool_output",
        "priority": 6,
        "token_count": tokens,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }


def _make_turn(user_text: str, assistant_text: str) -> list:
    return [HumanMessage(content=user_text), AIMessage(content=assistant_text)]


def test_context_reset_writes_bundle_and_trims_turns(tmp_path, monkeypatch) -> None:
    config = ContextEngineConfig(
        metrics_enabled=False,
        context_window_max=1000,
        optimal_context_size=900,
    )
    config.context_reset_enabled = True
    config.context_reset_root = str(tmp_path)
    config.context_reset_trigger_tokens = 200
    config.context_reset_keep_turns = 2
    config.context_reset_min_user_turns = 1

    engine = ContextEngine(config, system_prompt="Base system prompt.")

    async def _stub_summary(prompt: str):
        return ("User Profile:\n- Stubbed summary.", 32)

    monkeypatch.setattr(engine.context_reset_agent, "_summarize_context", _stub_summary)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    turns = []
    turns.extend(_make_turn("User turn one " * 10, "Assistant reply one " * 10))
    turns.extend(_make_turn("User turn two " * 10, "Assistant reply two " * 10))
    turns.extend(_make_turn("User turn three " * 10, "Assistant reply three " * 10))
    state["messages"] = turns
    state["context_segments"] = [_make_segment("seg-1", tokens=250)]

    result = asyncio.run(engine.pre_process(state))

    assert result["context_reset_count"] == 1
    reset_meta = result["last_context_reset"]
    assert "history" in reset_meta["history_path"]
    assert Path(reset_meta["history_path"]).exists()
    assert Path(reset_meta["trimmed_history_path"]).exists()
    assert Path(reset_meta["system_prompt_path"]).exists()

    assert "history" in result["system_prompt_addendum"].lower()
    segment_types = {seg["type"] for seg in result["context_segments"]}
    assert "context_reset_summary" in segment_types
    assert "context_reset_history" in segment_types

    # Keep last 2 user turns (each turn has user + assistant)
    assert len(result["messages"]) == 4

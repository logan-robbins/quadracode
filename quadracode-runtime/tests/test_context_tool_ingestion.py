from langchain_core.messages import ToolMessage

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import make_initial_context_engine_state, get_segment


def test_context_tool_captures_tool_message_output() -> None:
    config = ContextEngineConfig(metrics_enabled=False)
    engine = ContextEngine(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    tool_message = ToolMessage(
        content="All agents healthy",
        name="agent_registry",
        tool_call_id="registry-call-1",
    )
    state["messages"] = [tool_message]

    result = engine.handle_tool_response_sync(state)

    assert result["context_segments"], "tool outputs should create context segments"
    segment = result["context_segments"][-1]
    assert segment["type"] == "tool_output:agent_registry"
    assert "All agents healthy" in segment["content"]
    # Verify the segment can be retrieved using helper function
    retrieved_segment = get_segment(result, segment["id"])
    assert retrieved_segment is not None
    assert retrieved_segment["id"] == segment["id"]
    assert segment["restorable_reference"] == "registry-call-1"
    assert result["metrics_log"], "tool ingestion should log metrics"
    assert result["metrics_log"][-1]["event"] == "tool_response"

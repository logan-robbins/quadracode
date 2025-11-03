import asyncio

from datetime import datetime, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.progressive_loader import ProgressiveContextLoader
from quadracode_runtime.state import make_initial_context_engine_state


def _make_message(content: str):
    class DummyMessage:
        def __init__(self, text: str) -> None:
            self.content = text

    return DummyMessage(content)


def test_loader_identifies_needs_from_intent() -> None:
    config = ContextEngineConfig()
    loader = ProgressiveContextLoader(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["messages"] = [_make_message("Please implement the API and add tests")]

    result = asyncio.run(loader.prepare_context(state))

    types = {segment["type"] for segment in result["context_segments"]}
    assert "code_context" in types
    assert "file_structure" in types
    assert "test_suite" in types

    for segment in result["context_segments"]:
        assert "placeholder" not in segment["content"].lower()


def test_loader_respects_capacity_and_queues_pending() -> None:
    config = ContextEngineConfig(context_window_max=1000)
    loader = ProgressiveContextLoader(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["messages"] = [_make_message("We observed an error and need stack traces")]
    state["context_window_used"] = 900

    result = asyncio.run(loader.prepare_context(state))

    pending = result["pending_context"]
    assert any(name in pending for name in {"stack_traces", "error_history"})
    queue = result["prefetch_queue"]
    assert any(entry["type"] in {"stack_traces", "error_history"} for entry in queue)


def test_loader_updates_working_memory_and_hierarchy() -> None:
    config = ContextEngineConfig()
    loader = ProgressiveContextLoader(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["messages"] = [_make_message("Design proposal please")]

    result = asyncio.run(loader.prepare_context(state))

    assert result["working_memory"], "Working memory should contain loaded segments"
    assert result["context_hierarchy"], "Hierarchy should track priorities"
    for segment in result["working_memory"].values():
        assert segment["token_count"] > 0


def test_loader_generates_search_segment() -> None:
    config = ContextEngineConfig()
    loader = ProgressiveContextLoader(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["messages"] = [_make_message("Investigate ProgressiveContextLoader implementation details")]

    result = asyncio.run(loader.prepare_context(state))

    search_segments = [segment for segment in result["context_segments"] if segment["type"] == "code_search_results"]
    assert search_segments, "Expected code search results segment"
    assert "ProgressiveContextLoader" in search_segments[0]["content"]

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict

import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

contracts_path = PROJECT_ROOT / "quadracode-contracts" / "src"
runtime_path = PROJECT_ROOT / "quadracode-runtime" / "src"
tools_path = PROJECT_ROOT / "quadracode-tools" / "src"
for path in (contracts_path, runtime_path, tools_path):
    if path.exists() and str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("SHARED_PATH", "/tmp")
os.environ.setdefault("MCP_REDIS_SERVER_URL", "http://localhost:8000/mcp")
os.environ.setdefault("MCP_REDIS_TRANSPORT", "streamable_http")
os.environ.setdefault("PERPLEXITY_API_KEY", "test-key")

import types

mcp_stub = types.ModuleType("quadracode_runtime.tools.mcp_loader")
mcp_stub.load_mcp_tools_sync = lambda: []
mcp_stub.aget_mcp_tools = lambda: []
sys.modules.setdefault("quadracode_runtime.tools.mcp_loader", mcp_stub)

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from quadracode_contracts import MessageEnvelope
from quadracode_runtime.profiles import ORCHESTRATOR_PROFILE
from quadracode_runtime.runtime import CHECKPOINTER, RuntimeRunner


class _FakeGraph:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, state: Dict[str, Any], config: Dict[str, Any]):
        cfg = config.setdefault("configurable", {})
        cfg.setdefault("checkpoint_ns", "")
        self.calls.append({"state": state, "config": config})

        channel_messages = [getattr(m, "content", m) for m in state["messages"]]
        checkpoint = {
            "id": str(len(self.calls)),
            "channel_values": {"messages": channel_messages},
            "versions": {},
            "channel_versions": {},
        }
        metadata = {"created_at": len(self.calls)}
        CHECKPOINTER.put(config, checkpoint, metadata, {})
        return {"messages": [AIMessage(content="ok")]}


@pytest.fixture()
def runtime_with_fake_graph(monkeypatch):
    fake = _FakeGraph()
    monkeypatch.setattr(
        "quadracode_runtime.runtime.build_graph", lambda prompt: fake
    )
    CHECKPOINTER.delete_thread("chat-test")
    runner = RuntimeRunner(ORCHESTRATOR_PROFILE)
    yield runner, fake
    CHECKPOINTER.delete_thread("chat-test")


def _make_envelope(message: str, chat_id: str = "chat-test") -> MessageEnvelope:
    return MessageEnvelope(
        sender="human",
        recipient="orchestrator",
        message=message,
        payload={"chat_id": chat_id},
    )


def test_chat_id_used_as_thread_id(runtime_with_fake_graph):
    runner, fake = runtime_with_fake_graph

    envelope = _make_envelope("Hello there")
    responses = asyncio.run(runner._process_envelope(envelope))

    assert len(fake.calls) == 1
    cfg = fake.calls[0]["config"]["configurable"]
    assert cfg["thread_id"] == "chat-test"
    assert cfg["checkpoint_ns"] == ""

    assert len(responses) == 1
    payload = responses[0].payload
    assert payload["chat_id"] == "chat-test"
    assert payload["thread_id"] == "chat-test"

    checkpoint_config = {"configurable": {"thread_id": "chat-test", "checkpoint_ns": ""}}
    assert CHECKPOINTER.get_tuple(checkpoint_config) is not None


def test_subsequent_calls_reuse_checkpoint(runtime_with_fake_graph):
    runner, fake = runtime_with_fake_graph

    first = _make_envelope("First message")
    asyncio.run(runner._process_envelope(first))

    second = _make_envelope("Second message")
    asyncio.run(runner._process_envelope(second))

    assert len(fake.calls) == 2
    cfg1 = fake.calls[0]["config"]["configurable"]
    cfg2 = fake.calls[1]["config"]["configurable"]
    assert cfg1["thread_id"] == cfg2["thread_id"] == "chat-test"

    state1 = fake.calls[0]["state"]["messages"]
    state2 = fake.calls[1]["state"]["messages"]
    assert isinstance(state1[0], HumanMessage)
    assert state1[0].content == "First message"
    assert isinstance(state2[0], HumanMessage)
    assert state2[0].content == "Second message"

    checkpoint_config = {"configurable": {"thread_id": "chat-test", "checkpoint_ns": ""}}
    saved = CHECKPOINTER.get_tuple(checkpoint_config)
    assert saved is not None

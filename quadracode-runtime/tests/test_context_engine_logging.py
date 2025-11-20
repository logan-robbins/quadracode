from __future__ import annotations

import asyncio
import importlib
import json

from quadracode_runtime import context_engine_logging
from quadracode_runtime.state import make_initial_context_engine_state


def test_log_context_compression_writes_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QUADRACODE_CONTEXT_ENGINE_LOG_DIR", str(tmp_path))
    importlib.reload(context_engine_logging)

    state = make_initial_context_engine_state(context_window_max=8_000)
    state["thread_id"] = "chat-demo"
    state["context_window_used"] = 1_200
    state["refinement_ledger"] = []

    before_text = "a" * 600
    after_text = "b" * 100

    asyncio.run(context_engine_logging.log_context_compression(
        state,
        action="compress",
        stage="context_curator.optimize",
        reason="unit_test",
        segment_id="seg-1",
        segment_type="tool_output",
        before_tokens=1_234,
        after_tokens=321,
        before_content=before_text,
        after_content=after_text,
        metadata={"source": "test"},
    ))

    log_path = tmp_path / "chat-demo.jsonl"
    assert log_path.exists()

    raw = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1
    entry = json.loads(raw[0])

    assert entry["thread_id"] == "chat-demo"
    assert entry["before_tokens"] == 1_234
    assert entry["after_tokens"] == 321
    assert entry["tokens_saved"] == 913
    assert entry["stage"] == "context_curator.optimize"
    assert entry["reason"] == "unit_test"
    assert entry["metadata"]["source"] == "test"
    assert entry["before_preview"].endswith("…")
    assert entry["after_preview"] == after_text


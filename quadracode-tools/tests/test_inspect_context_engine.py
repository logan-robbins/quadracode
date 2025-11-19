from __future__ import annotations

import json
from datetime import datetime, timezone

from quadracode_tools.tools.context_engine import inspect_context_engine


def _write_jsonl(path, entries):
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry))
            handle.write("\n")


def test_inspect_context_engine_reports_combined_history(tmp_path, monkeypatch) -> None:
    thread_id = "chat-case"
    time_travel_dir = tmp_path / "time_travel_logs"
    compression_dir = tmp_path / "context_engine_logs"
    time_travel_dir.mkdir()
    compression_dir.mkdir()

    timestamp = datetime.now(timezone.utc).isoformat()
    _write_jsonl(
        time_travel_dir / f"{thread_id}.jsonl",
        [
            {
                "timestamp": timestamp,
                "event": "stage.pre_process",
                "payload": {"context_window_used": 900, "context_segments": 12, "quality_score": 0.82},
                "exhaustion_mode": "none",
            },
            {
                "timestamp": timestamp,
                "event": "exhaustion_update",
                "payload": {"from": "none", "to": "context_saturation", "action": "context_compaction"},
            },
        ],
    )

    _write_jsonl(
        compression_dir / f"{thread_id}.jsonl",
        [
            {
                "timestamp": timestamp,
                "action": "compress",
                "stage": "context_curator.optimize",
                "reason": "curator_compress",
                "segment_id": "seg-1",
                "segment_type": "tool_output",
                "before_tokens": 1200,
                "after_tokens": 400,
                "compression_ratio": 0.33,
            }
        ],
    )

    monkeypatch.setenv("QUADRACODE_TIME_TRAVEL_DIR", str(time_travel_dir))
    monkeypatch.setenv("QUADRACODE_CONTEXT_ENGINE_LOG_DIR", str(compression_dir))

    output = inspect_context_engine.invoke({"thread_id": thread_id, "last_n_events": 5})

    assert "Stage timeline" in output
    assert "Compression history" in output
    assert "1200->400" in output or "1200->400" in output.replace(" ", "")
    assert "Total tokens saved=800" in output


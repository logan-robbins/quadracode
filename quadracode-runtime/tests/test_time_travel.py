import asyncio
import json
from pathlib import Path

import pytest

from quadracode_runtime.state import PRPState, make_initial_context_engine_state
from quadracode_runtime.time_travel import (
    TimeTravelRecorder,
    diff_cycles,
    replay_cycle,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _read_entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_time_travel_recorder_logs_stage_events(tmp_path):
    recorder = TimeTravelRecorder(base_dir=tmp_path)
    state = make_initial_context_engine_state(context_window_max=1000)
    state["thread_id"] = "test-thread"
    state["prp_state"] = PRPState.HYPOTHESIZE
    recorder.log_stage(state, stage="pre_process", payload={"quality": 0.8})

    log_file = tmp_path / "test-thread.jsonl"
    assert log_file.exists()
    entries = _read_entries(log_file)
    assert entries and entries[0]["event"] == "stage.pre_process"
    replayed = replay_cycle(log_file, entries[0]["cycle_id"])
    assert replayed, "replay should return recorded events"


@pytest.mark.anyio("asyncio")
async def test_time_travel_recorder_async_schedule(tmp_path):
    recorder = TimeTravelRecorder(base_dir=tmp_path)
    state = make_initial_context_engine_state(context_window_max=1000)
    state["thread_id"] = "async-thread"
    state["prp_state"] = PRPState.HYPOTHESIZE

    recorder.log_stage(state, stage="pre_process", payload={"quality": 0.9})
    if recorder._pending_writes:
        await asyncio.gather(*recorder._pending_writes)

    log_file = tmp_path / "async-thread.jsonl"
    assert log_file.exists()
    entries = _read_entries(log_file)
    assert entries and entries[0]["event"] == "stage.pre_process"
    assert not recorder._pending_writes


def test_time_travel_diff_cycles(tmp_path):
    log_file = tmp_path / "session.jsonl"
    entries = [
        {
            "timestamp": "2024-01-01T00:00:00Z",
            "event": "cycle_snapshot",
            "cycle_id": "cycle-1",
            "payload": {
                "status": "in_progress",
                "cycle_metrics": {
                    "total_tokens": 1000,
                    "tool_calls": 2,
                    "stage_usage": [{"stage": "pre_process", "tokens": 600}],
                },
            },
        },
        {
            "timestamp": "2024-01-01T00:05:00Z",
            "event": "cycle_snapshot",
            "cycle_id": "cycle-2",
            "payload": {
                "status": "succeeded",
                "cycle_metrics": {
                    "total_tokens": 1500,
                    "tool_calls": 3,
                    "stage_usage": [{"stage": "pre_process", "tokens": 800}, {"stage": "handle_tool_response", "tokens": 700}],
                },
            },
        },
    ]
    log_file.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")
    result = diff_cycles(log_file, "cycle-1", "cycle-2")
    assert result["delta"]["tokens_delta"] == 500
    assert result["delta"]["tool_calls_delta"] == 1
    assert result["delta"]["status_change"]["to"] == "succeeded"

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.state import ContextSegment, make_initial_context_engine_state
from quadracode_runtime.nodes.context_curator import ContextCurator


def _make_segment(**overrides: object) -> ContextSegment:
    base: ContextSegment = {
        "id": "seg-1",
        "content": "line one\nline two\nline three",
        "type": "conversation",
        "priority": 5,
        "token_count": 120,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }
    base.update(overrides)
    return base


def test_optimize_compresses_low_scoring_segment() -> None:
    config = ContextEngineConfig(target_context_size=10_000)
    curator = ContextCurator(config)

    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    segment = _make_segment(
        id="seg-compress",
        priority=1,
        token_count=400,
        timestamp=old_timestamp,
    )

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [segment]

    result = asyncio.run(curator.optimize(state))

    assert len(result["context_segments"]) == 1
    compressed = result["context_segments"][0]
    assert compressed["id"] == "seg-compress"
    assert compressed["token_count"] <= segment["token_count"] // 2
    assert not compressed["compression_eligible"]


def test_optimize_externalizes_when_over_target() -> None:
    config = ContextEngineConfig(target_context_size=150)
    curator = ContextCurator(config)

    high_priority = _make_segment(
        id="seg-important",
        priority=8,
        token_count=200,
        type="memory",
        content="fact one\nfact two\nfact three",
    )
    medium_priority = _make_segment(
        id="seg-secondary",
        priority=4,
        token_count=80,
        type="tool_output",
    )

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [high_priority, medium_priority]

    result = asyncio.run(curator.optimize(state))

    # The high priority segment should be externalized to stay within the target window
    pointer = next(seg for seg in result["context_segments"] if seg["id"] == "seg-important")
    assert pointer["type"].startswith("pointer:")
    assert pointer["restorable_reference"] is not None
    assert pointer["token_count"] < high_priority["token_count"]

    ref_id = pointer["restorable_reference"]
    assert ref_id in result["external_memory_index"]
    path = result["external_memory_index"][ref_id]
    assert path.endswith(".json")
    assert "seg-important" in path


def test_externalize_segment_persists_file(tmp_path) -> None:
    config = ContextEngineConfig(
        target_context_size=100,
        external_memory_path=str(tmp_path),
        externalize_write_enabled=True,
    )
    curator = ContextCurator(config)

    segment = _make_segment(
        id="seg-ext",
        type="memory",
        content="deep diagnostic details",
        token_count=120,
    )

    pointer, reference = curator._externalize_segment(segment)

    assert pointer["type"].startswith("pointer:"), "Externalized segment should become pointer"
    assert pointer["restorable_reference"] is not None

    path = Path(reference["path"])
    assert path.exists(), "Externalized segment should be written to disk"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["segment"]["id"] == segment["id"]
    assert payload["segment"]["content"] == segment["content"]
    assert payload["segment"]["type"] == segment["type"]


def test_optimize_discards_low_priority_when_over_target() -> None:
    config = ContextEngineConfig(target_context_size=50)
    curator = ContextCurator(config)

    important = _make_segment(id="seg-keep", priority=7, token_count=40)
    expendable = _make_segment(
        id="seg-drop",
        priority=1,
        token_count=30,
        timestamp=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
    )

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [important, expendable]

    result = asyncio.run(curator.optimize(state))

    ids = [segment["id"] for segment in result["context_segments"]]
    assert "seg-keep" in ids
    assert "seg-drop" not in ids


def test_post_decision_curation_prunes_stale_segments() -> None:
    config = ContextEngineConfig()
    curator = ContextCurator(config)

    stale_timestamp = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    fresh_timestamp = datetime.now(timezone.utc).isoformat()

    stale = _make_segment(id="seg-old", timestamp=stale_timestamp)
    fresh = _make_segment(id="seg-new", timestamp=fresh_timestamp)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [stale, fresh]
    state["context_quality_score"] = 0.6

    optimized = asyncio.run(curator.optimize(state))
    post = asyncio.run(curator.post_decision_curation(optimized))

    ids = [segment["id"] for segment in post["context_segments"]]
    assert "seg-new" in ids
    assert "seg-old" not in ids

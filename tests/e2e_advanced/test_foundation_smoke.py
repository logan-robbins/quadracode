"""Smoke tests for foundation test infrastructure.

These tests validate that the test infrastructure is set up correctly
without requiring the full Docker stack or long execution times.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

# Import utilities to verify they work
from .utils.agent_helpers import list_agents
from .utils.artifacts import capture_context_metrics
from .utils.logging_framework import (
    create_test_log_directory,
    log_stream_snapshot,
    log_turn,
)
from .utils.metrics_collector import MetricsCollector
from .utils.redis_helpers import get_stream_stats
from .utils.timeouts import TimeoutManager, wait_for_condition


def test_logging_infrastructure(tmp_path):
    """Validate logging framework works correctly."""
    # Create log directory
    log_dir = create_test_log_directory("test_smoke", base_dir=tmp_path)
    assert log_dir.exists()
    assert (log_dir / "test.log").exists()

    # Test turn logging
    message = {"sender": "human", "message": "test"}
    response = {"sender": "orchestrator", "message": "response"}
    log_turn(log_dir, 1, message, response, duration_ms=1000)

    turn_file = log_dir / "turn_001.json"
    assert turn_file.exists()

    # Test stream snapshot
    entries = [("123-0", {"sender": "test", "message": "hello"})]
    log_stream_snapshot(log_dir, "qc:mailbox/test", entries)

    snapshot_file = log_dir / "qc_mailbox_test_snapshot.json"
    assert snapshot_file.exists()

    print("✓ Logging infrastructure validated")


def test_metrics_collector_workflow():
    """Validate complete MetricsCollector workflow."""
    collector = MetricsCollector("test_smoke", "run123")

    # Record some events
    collector.record_false_stop(
        {"stream_id": "1-0", "message": "Done"},
        detected_by="humanclone",
        stage="incomplete_implementation",
        recovery_time_ms=45000,
    )

    collector.record_humanclone_invocation(
        {"message": "Done"},
        {"exhaustion_mode": "TEST_FAILURE", "rationale": "Tests failing"},
        outcome="rejection",
        latency_ms=15000,
        exhaustion_mode="TEST_FAILURE",
    )

    collector.record_prp_transition("TEST", "CONCLUDE", valid=True)
    collector.record_tool_call("workspace_exec", 250.5, success=True)
    collector.record_message("orchestrator", "human")

    # Compute metrics
    collector.metrics["humanclone"]["correct_rejections"] = 1
    collector.compute_derived_metrics()

    # Validate calculations
    assert collector.metrics["false_stops"]["total"] == 1
    assert collector.metrics["humanclone"]["total_invocations"] == 1
    assert collector.metrics["resources"]["tool_calls_total"] == 1

    # Validate consistency
    errors = collector.validate_consistency()
    assert len(errors) == 0, f"Consistency errors: {errors}"

    print("✓ MetricsCollector workflow validated")
    print(f"  - False-stops: {collector.metrics['false_stops']['total']}")
    print(f"  - HumanClone invocations: {collector.metrics['humanclone']['total_invocations']}")
    print(f"  - PRP transitions: {len(collector.metrics['prp']['transition_counts'])}")
    print(f"  - Tool calls: {collector.metrics['resources']['tool_calls_total']}")


def test_timeout_manager_integration():
    """Validate TimeoutManager works as expected."""
    with TimeoutManager(timeout=5, operation="smoke test") as tm:
        time.sleep(0.1)
        tm.checkpoint("step 1")
        time.sleep(0.1)
        tm.checkpoint("step 2")

    assert not tm.timed_out
    assert len(tm.checkpoints) == 2
    assert tm.elapsed() >= 0.2
    remaining = tm.remaining()
    assert remaining < 5.0

    print("✓ TimeoutManager validated")
    print(f"  - Elapsed: {tm.elapsed():.2f}s")
    print(f"  - Checkpoints: {len(tm.checkpoints)}")


def test_polling_utilities():
    """Validate polling utilities work correctly."""
    call_count = 0

    def condition():
        nonlocal call_count
        call_count += 1
        return call_count >= 3

    result = wait_for_condition(condition, timeout=5, poll_interval=0.1)
    assert result is True
    assert call_count >= 3

    print("✓ Polling utilities validated")
    print(f"  - Condition checks: {call_count}")


def test_artifact_capture(tmp_path):
    """Validate artifact capture utilities."""
    # Test context metrics capture
    entries = [
        ("123-0", {"event": "pre_process", "payload": '{"tokens": 100}'}),
        ("124-0", {"event": "load", "payload": '{"segments": ["test"]}'}),
    ]

    output_path = tmp_path / "context_metrics.json"
    capture_context_metrics(output_path, entries)

    assert output_path.exists()

    with output_path.open() as f:
        data = json.load(f)

    assert data["total_events"] == 2
    assert "pre_process" in data["events_by_type"]
    assert "load" in data["events_by_type"]

    print("✓ Artifact capture validated")
    print(f"  - Metrics events captured: {data['total_events']}")


def test_imports_and_structure():
    """Validate all critical imports work."""
    # This test just validates imports don't raise errors
    from .utils import (
        agent_helpers,
        artifacts,
        llm_judge,
        logging_framework,
        metrics_collector,
        redis_helpers,
        timeouts,
    )

    # Validate key classes/functions exist
    assert hasattr(metrics_collector, "MetricsCollector")
    assert hasattr(llm_judge, "LLMJudge")
    assert hasattr(agent_helpers, "spawn_agent")
    assert hasattr(redis_helpers, "wait_for_message_on_stream")
    assert hasattr(timeouts, "TimeoutManager")

    print("✓ All module imports validated")


def test_metrics_export_and_schema(tmp_path):
    """Validate metrics can be exported and match expected structure."""
    collector = MetricsCollector("test_smoke", "run123")

    # Add minimal data
    collector.record_false_stop(
        {"stream_id": "1-0"},
        detected_by="humanclone",
        stage="incomplete_implementation",
    )
    collector.compute_derived_metrics()

    # Export
    output_path = tmp_path / "metrics.json"
    collector.export(output_path)

    assert output_path.exists()

    # Validate structure
    with output_path.open() as f:
        metrics = json.load(f)

    # Check required top-level keys
    required_keys = [
        "test_name",
        "run_id",
        "start_time",
        "end_time",
        "duration_ms",
        "success",
        "false_stops",
        "humanclone",
        "prp",
        "resources",
        "completion",
    ]

    for key in required_keys:
        assert key in metrics, f"Missing required key: {key}"

    # Validate nested structure
    assert isinstance(metrics["false_stops"]["instances"], list)
    assert isinstance(metrics["humanclone"]["trigger_details"], list)
    assert isinstance(metrics["prp"]["transition_counts"], dict)
    assert isinstance(metrics["resources"]["tool_calls_by_type"], dict)

    print("✓ Metrics export and structure validated")
    print(f"  - All {len(required_keys)} required keys present")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])


"""Smoke tests for foundation test infrastructure.

These tests validate that the test infrastructure is set up correctly
while the full Docker Compose stack is running and healthy, but they do so
with minimal runtime compared to the long E2E scenarios.
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


@pytest.mark.e2e_advanced
def test_checkpoint_persistence_across_restart(docker_stack, tmp_path):
    """Validate checkpoint survives orchestrator container restart.
    
    This test verifies that LangGraph checkpoints are properly persisted
    and restored when the orchestrator container is restarted, ensuring
    conversation state is maintained across failures.
    
    Args:
        docker_stack: Pytest fixture that brings up Docker Compose stack
        tmp_path: Pytest fixture providing temporary directory
    """
    import json
    import time
    import sys
    from pathlib import Path
    
    # Import required utilities from parent test suite
    parent_tests = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(parent_tests))
    from test_end_to_end import (
        get_last_stream_id,
        read_stream_after,
        run_compose,
        send_message_to_orchestrator,
        wait_for_human_response,
    )
    
    def _extract_ai_contents(payload_raw: str) -> list[str]:
        """Extract AI message contents from payload."""
        payload = json.loads(payload_raw)
        messages = payload.get("messages", [])
        outputs: list[str] = []
        for entry in messages:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "ai":
                continue
            data = entry.get("data")
            if isinstance(data, dict):
                content = data.get("content")
                if isinstance(content, str):
                    outputs.append(content.strip())
        return [text for text in outputs if text]
    
    print("\n" + "=" * 70)
    print("TEST: Checkpoint Persistence Across Restart")
    print("=" * 70)
    
    # Send initial message with information to remember
    baseline_human = get_last_stream_id("qc:mailbox/human")
    metrics_baseline = get_last_stream_id("qc:context:metrics")
    
    print("→ Sending initial message with code name 'Orion'")
    send_message_to_orchestrator("Remember that my project code name is Orion.", reply_to="agent-runtime")
    first_response = wait_for_human_response(baseline_human)
    first_payload = first_response.get("payload") or ""
    first_ai_contents = _extract_ai_contents(first_payload)
    assert first_ai_contents, "Initial orchestrator turn produced no AI content"
    print(f"✓ Initial response received: {len(first_ai_contents)} AI messages")
    
    # Restart orchestrator container to verify checkpoint restoration
    print("→ Restarting orchestrator container...")
    run_compose(["restart", "orchestrator-runtime"])
    time.sleep(10)  # Give container time to restart and resume polling
    print("✓ Orchestrator container restarted")
    
    # Query the remembered information
    second_baseline = get_last_stream_id("qc:mailbox/human")
    print("→ Asking for code name after restart")
    send_message_to_orchestrator("What is my project code name?", reply_to="agent-runtime")
    second_response = wait_for_human_response(second_baseline)
    second_payload = second_response.get("payload") or ""
    second_ai_contents = _extract_ai_contents(second_payload)
    assert second_ai_contents, "Follow-up orchestrator turn produced no AI content after restart"
    
    combined_lower = " ".join(second_ai_contents).lower()
    assert "orion" in combined_lower, f"Orchestrator failed to recall project code name after restart. Response: {combined_lower}"
    print(f"✓ Checkpoint restored successfully - code name 'Orion' recalled")
    
    # Ensure context metrics continued after restart
    metrics_after = read_stream_after("qc:context:metrics", metrics_baseline, count=400)
    post_process_events = [entry for entry in metrics_after if entry[1].get("event") == "post_process"]
    assert post_process_events, "Context metrics did not resume after orchestrator restart"
    print(f"✓ Context metrics resumed: {len(post_process_events)} post_process events")
    
    print("=" * 70)
    print("✓ TEST PASSED: Checkpoint persistence validated")
    print("=" * 70)


@pytest.mark.e2e_advanced
def test_workspace_volume_inheritance(tmp_path):
    """Validate spawned agents inherit workspace volumes correctly.
    
    This test verifies that when an agent is spawned with workspace
    configuration, the workspace volume is properly mounted in the
    agent container at the expected mount path.
    
    Args:
        tmp_path: Pytest fixture providing temporary directory
    """
    import json
    import os
    import shutil
    import subprocess
    import time
    from pathlib import Path
    
    from quadracode_tools.tools.workspace import ensure_workspace, workspace_destroy
    
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI must be installed and available on PATH for workspace mount test")
    
    print("\n" + "=" * 70)
    print("TEST: Workspace Volume Inheritance")
    print("=" * 70)
    
    # Ensure agent image is available
    agent_image = "quadracode-agent"
    image_check = subprocess.run(["docker", "image", "inspect", agent_image], capture_output=True, text=True)
    if image_check.returncode != 0:
        print("→ Building agent image...")
        build = subprocess.run(["docker", "compose", "build", "agent-runtime"], capture_output=True, text=True)
        if build.returncode != 0:
            pytest.fail(
                f"Failed to build agent image (docker compose build agent-runtime).\nstdout: {build.stdout}\nstderr: {build.stderr}"
            )
        print("✓ Agent image built")
    
    # Create workspace
    workspace_id = f"ws-test-{int(time.time())}"
    print(f"→ Creating workspace: {workspace_id}")
    success, descriptor, error = ensure_workspace(workspace_id, image="python:3.12-slim", network="bridge")
    if not success or descriptor is None:
        pytest.fail(f"Unable to provision workspace for test: {error}")
    print(f"✓ Workspace created: {descriptor.volume} -> {descriptor.mount_path}")
    
    agent_container = None
    try:
        # Spawn agent with workspace configuration
        env = os.environ.copy()
        env.update(
            {
                "QUADRACODE_WORKSPACE_VOLUME": descriptor.volume,
                "QUADRACODE_WORKSPACE_ID": workspace_id,
                "QUADRACODE_WORKSPACE_MOUNT": descriptor.mount_path,
            }
        )
        spawn_script = Path("scripts/agent-management/spawn-agent.sh")
        print(f"→ Spawning agent with workspace volume...")
        result = subprocess.run(
            [str(spawn_script), "", agent_image, "bridge"],
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode != 0:
            pytest.fail(f"Unable to spawn agent container: {result.stderr.strip() or result.stdout}")
        
        payload = json.loads(result.stdout)
        assert payload.get("success"), f"Spawn script reported failure: {payload}"
        agent_container = payload.get("container_name")
        assert agent_container, "Spawn script did not return container name"
        print(f"✓ Agent spawned: {agent_container}")
        
        # Verify workspace volume is mounted
        print(f"→ Inspecting container mounts...")
        inspect = subprocess.run(
            [
                "docker",
                "inspect",
                agent_container,
                "--format",
                "{{json .Mounts}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        mounts = json.loads(inspect.stdout)
        has_workspace_mount = any(
            mount.get("Destination") == descriptor.mount_path and mount.get("Name") == descriptor.volume
            for mount in mounts
        )
        assert has_workspace_mount, (
            f"Workspace volume {descriptor.volume} not mounted at {descriptor.mount_path} in {agent_container}. "
            f"Mounts found: {mounts}"
        )
        print(f"✓ Workspace volume correctly mounted: {descriptor.volume} -> {descriptor.mount_path}")
        
    finally:
        # Cleanup
        if agent_container:
            print(f"→ Cleaning up agent container: {agent_container}")
            subprocess.run(["docker", "rm", "-f", agent_container], capture_output=True)
        print(f"→ Cleaning up workspace: {workspace_id}")
        workspace_destroy.invoke({"workspace_id": workspace_id, "delete_volume": True})
    
    print("=" * 70)
    print("✓ TEST PASSED: Workspace volume inheritance validated")
    print("=" * 70)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])


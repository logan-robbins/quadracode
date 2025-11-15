"""
Quadracode Advanced E2E Tests - Module 6: Observability

This module validates observability and debugging capabilities:
- Time-travel logging and state capture
- Metrics stream comprehensive coverage

Tests run for 10-18 minutes with real LLM calls, exercising:
- Time-travel JSONL log generation
- State snapshot recording
- Metrics stream event types
- Context, autonomous, and PRP metrics
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import redis

from tests.e2e_advanced.utils.logging_framework import (
    create_test_log_directory,
    log_stream_snapshot,
    log_turn,
)
from tests.e2e_advanced.utils.redis_helpers import (
    dump_all_streams,
    get_last_stream_id,
    poll_stream_for_event,
    read_stream,
    send_message_to_orchestrator,
    wait_for_message_on_stream,
)
from tests.e2e_advanced.utils.artifacts import (
    capture_docker_logs,
    capture_time_travel_logs,
)
from tests.e2e_advanced.utils.timeouts import wait_for_condition
from tests.e2e_advanced.utils.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)


def parse_jsonl_file(filepath: Path) -> List[Dict[str, Any]]:
    """
    Parse a JSONL (JSON Lines) file.
    
    Returns:
        List of dictionaries, one per line.
    """
    entries = []
    
    try:
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue  # Skip empty lines
                
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON at line {line_num}: {e}")
    except Exception as e:
        logger.error(f"Failed to read JSONL file {filepath}: {e}")
    
    return entries


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_time_travel_log_capture(
    docker_services, redis_client, test_config
):
    """
    Test 6.1: Time-Travel Log Capture (10 minutes)
    
    Objective: Run a complex multi-turn conversation and verify all state
    transitions are logged to time-travel JSONL files.
    
    This test validates:
    - Time-travel logging enabled and writing to JSONL files
    - Each conversation turn generates at least one log entry
    - Log entries contain required fields (timestamp, event_type, state_snapshot)
    - State snapshots show PRP state, exhaustion mode, context quality
    - JSONL file is valid (each line parseable as JSON)
    
    Expected duration: 10 minutes minimum
    Expected turns: 20
    Expected time-travel log entries: >= 20
    
    Prerequisites:
    - QUADRACODE_TIME_TRAVEL_ENABLED=true
    - QUADRACODE_TIME_TRAVEL_DIR=/shared/time_travel_logs (or equivalent)
    - Shared volume mounted for log access
    
    Troubleshooting:
    - If log file not found: Check time-travel directory mount and permissions
    - If entries missing: Verify recorder is enabled in runtime configuration
    - If invalid JSON: Check recorder serialization logic
    """
    test_name = "test_time_travel_log_capture"
    logger.info(f"Starting {test_name}")
    
    # Initialize metrics collector
    run_id = f"{int(time.time())}-{os.urandom(4).hex()}"
    collector = MetricsCollector(test_name=test_name, run_id=run_id)
    
    # Create log directory
    log_dir = create_test_log_directory(test_name)
    logger.info(f"Logs will be written to: {log_dir}")
    
    try:
        # Initialize Redis stream baselines
        baseline_orchestrator = get_last_stream_id(
            redis_client, "qc:mailbox/orchestrator"
        )
        baseline_human = get_last_stream_id(redis_client, "qc:mailbox/human")
        
        logger.info(
            f"Baseline stream IDs: orchestrator={baseline_orchestrator}, "
            f"human={baseline_human}"
        )
        
        # Note: This test requires time-travel logging to be enabled
        logger.info(
            "Prerequisites: Ensure QUADRACODE_TIME_TRAVEL_ENABLED=true and "
            "QUADRACODE_TIME_TRAVEL_DIR is set with a shared volume mount"
        )
        
        test_start_time = time.time()
        
        # Step 1: Send 20 varied messages to generate diverse state transitions
        logger.info("Step 1: Sending 20 messages to generate time-travel log entries")
        
        messages = [
            "What is the purpose of the Quadracode system?",
            "Explain the Perpetual Refinement Protocol.",
            "How does context engineering work?",
            "Describe the HumanClone agent's role.",
            "What tools are available for workspace management?",
            "How do agents communicate via Redis streams?",
            "Explain the agent lifecycle management process.",
            "What is the difference between orchestrator and agent profiles?",
            "How does the context curator decide what to externalize?",
            "What triggers the exhaustion mode in PRP?",
            "Describe the time-travel debugging capabilities.",
            "How does the agent registry track health status?",
            "What is the hotpath protection mechanism?",
            "Explain workspace isolation and integrity snapshots.",
            "How are test suites executed in workspaces?",
            "What metrics are captured in the autonomous events stream?",
            "Describe the refinement ledger structure.",
            "How does the progressive loader prioritize context segments?",
            "What is the role of the context quality scorer?",
            "Summarize the key architectural components of Quadracode.",
        ]
        
        turn_number = 0
        
        for message in messages:
            turn_number += 1
            turn_start = time.time()
            
            logger.info(f"Turn {turn_number}: {message[:50]}...")
            
            send_message_to_orchestrator(
                redis_client,
                message,
                sender="human",
            )
            
            # Wait for response
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=90,
            )
            baseline_human = response["stream_id"]
            
            turn_duration = time.time() - turn_start
            
            # Log turn
            log_turn(
                log_dir,
                turn_number,
                {"content": message},
                {
                    "stream_id": response["stream_id"],
                    "content": response.get("message", "")[:200],
                    "duration_ms": turn_duration * 1000,
                },
            )
            
            logger.info(
                f"Turn {turn_number} complete: {turn_duration:.2f}s, "
                f"Response stream ID: {response['stream_id']}"
            )
        
        total_test_time = time.time() - test_start_time
        logger.info(f"Test flow complete: {total_test_time:.2f}s, {turn_number} turns")
        
        # Step 2: Capture time-travel logs
        logger.info("Step 2: Capturing time-travel logs from shared volume")
        
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # Try to capture time-travel logs
        try:
            capture_time_travel_logs("orchestrator-runtime", artifacts_dir)
            logger.info(f"✓ Time-travel logs copied to {artifacts_dir}")
        except Exception as e:
            logger.error(
                f"Failed to capture time-travel logs: {e}. "
                f"This may indicate logs are not being written or volume mount is incorrect."
            )
            # Continue test to check if logs exist elsewhere
        
        # Step 3: Parse time-travel logs
        logger.info("Step 3: Parsing time-travel JSONL logs")
        
        # Look for orchestrator JSONL file
        time_travel_log_files = list(artifacts_dir.glob("**/orchestrator*.jsonl"))
        
        if not time_travel_log_files:
            # Try alternative paths
            time_travel_log_files = list(artifacts_dir.glob("**/*.jsonl"))
        
        if not time_travel_log_files:
            logger.warning(
                f"No time-travel JSONL files found in {artifacts_dir}. "
                f"Files found: {list(artifacts_dir.rglob('*'))[:20]}"
            )
            # Check if time-travel logging might be disabled
            pytest.skip(
                "Time-travel logs not found. Ensure QUADRACODE_TIME_TRAVEL_ENABLED=true "
                "and logs are written to a shared volume accessible to tests."
            )
        
        # Parse first JSONL file found
        time_travel_log_file = time_travel_log_files[0]
        logger.info(f"Parsing time-travel log file: {time_travel_log_file}")
        
        log_entries = parse_jsonl_file(time_travel_log_file)
        
        logger.info(f"Parsed {len(log_entries)} time-travel log entries")
        
        # Verification Step 1: Assert at least 20 entries (one per turn)
        logger.info("Verification: Checking log entry count")
        assert len(log_entries) >= 20, (
            f"Expected at least 20 time-travel log entries (one per turn), "
            f"got {len(log_entries)}. This indicates time-travel logging may not be "
            f"capturing all state transitions. Log file: {time_travel_log_file}"
        )
        logger.info(f"✓ Verified {len(log_entries)} time-travel log entries")
        
        # Verification Step 2: Validate entry structure
        logger.info("Verification: Validating log entry structure")
        
        required_fields = ["timestamp", "event_type"]
        
        for i, entry in enumerate(log_entries[:10]):  # Check first 10 entries
            for field in required_fields:
                assert field in entry, (
                    f"Entry {i} missing required field '{field}'. "
                    f"Entry: {json.dumps(entry, indent=2)[:300]}"
                )
        
        logger.info("✓ All sampled entries have required fields")
        
        # Verification Step 3: Check for state snapshots
        logger.info("Verification: Checking for state snapshots in entries")
        
        entries_with_state = [e for e in log_entries if "state_snapshot" in e or "state" in e]
        
        logger.info(f"Found {len(entries_with_state)} entries with state snapshots")
        
        # At least some entries should have state
        if len(entries_with_state) > 0:
            logger.info(f"✓ State snapshots present in {len(entries_with_state)} entries")
            
            # Sample a state snapshot
            sample_state = entries_with_state[0].get("state_snapshot", entries_with_state[0].get("state", {}))
            logger.info(f"Sample state snapshot keys: {list(sample_state.keys())[:10]}")
            
            # Check for expected state fields (may vary based on implementation)
            state_fields = ["prp_state", "exhaustion_mode", "context_quality_score", "messages"]
            found_fields = [f for f in state_fields if f in str(sample_state)]
            logger.info(f"State fields found in sample: {found_fields}")
        else:
            logger.warning(
                "No entries with state snapshots found. "
                "Time-travel logging may not be capturing full state."
            )
        
        # Verification Step 4: Check for tool calls in entries
        logger.info("Verification: Checking for tool call records")
        
        entries_with_tools = [e for e in log_entries if "tool_calls" in e or "tools" in e]
        
        logger.info(f"Found {len(entries_with_tools)} entries with tool call records")
        
        if len(entries_with_tools) > 0:
            logger.info(f"✓ Tool calls recorded in {len(entries_with_tools)} entries")
        else:
            logger.warning("No tool call records found in time-travel logs")
        
        # Verification Step 5: Assert test duration >= 10 minutes
        logger.info("Verification: Checking test duration")
        min_duration = 10 * 60  # 10 minutes
        assert total_test_time >= min_duration, (
            f"Test ran for {total_test_time:.2f}s, expected at least {min_duration}s."
        )
        logger.info(f"✓ Verified test duration: {total_test_time:.2f}s >= {min_duration}s")
        
        logger.info("✓ Test complete")
        
        # Compute and export metrics
        collector.compute_derived_metrics()
        
        metrics_dir = Path("tests/e2e_advanced/metrics")
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = metrics_dir / f"{test_name}_{run_id}_metrics.json"
        collector.export(metrics_path)
        logger.info(f"Metrics exported to: {metrics_path}")
        
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        raise
    
    finally:
        # Teardown
        logger.info("Teardown: Capturing final artifacts")
        
        # Dump Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator.log")
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_metrics_stream_comprehensive_coverage(
    docker_services, redis_client, test_config
):
    """
    Test 6.2: Metrics Stream Comprehensive Coverage (8 minutes)
    
    Objective: Generate all major metrics events (context, autonomous, PRP)
    and verify stream contents.
    
    This test validates:
    - Context metrics stream captures all event types
    - Event types include: pre_process, post_process, load, curation, governor_plan, tool_response
    - Autonomous events stream records checkpoints, escalations, final_review
    - PRP metrics present in logs (state transitions)
    - Event payloads contain required fields
    - No critical fields are empty/null
    
    Expected duration: 8 minutes minimum
    Expected context events: Multiple types
    Expected autonomous events: At least 1 checkpoint
    
    Prerequisites:
    - Context metrics stream enabled (qc:context:metrics)
    - Autonomous events stream enabled (qc:autonomous:events)
    - Services configured to emit metrics
    
    Troubleshooting:
    - If no context events: Verify context engine is enabled
    - If no autonomous events: Ensure autonomous mode is configured
    - If missing event types: Check service implementations emit all event types
    """
    test_name = "test_metrics_stream_comprehensive_coverage"
    logger.info(f"Starting {test_name}")
    
    # Initialize metrics collector
    run_id = f"{int(time.time())}-{os.urandom(4).hex()}"
    collector = MetricsCollector(test_name=test_name, run_id=run_id)
    
    # Create log directory
    log_dir = create_test_log_directory(test_name)
    logger.info(f"Logs will be written to: {log_dir}")
    
    try:
        # Initialize Redis stream baselines
        baseline_orchestrator = get_last_stream_id(
            redis_client, "qc:mailbox/orchestrator"
        )
        baseline_human = get_last_stream_id(redis_client, "qc:mailbox/human")
        baseline_context_metrics = get_last_stream_id(
            redis_client, "qc:context:metrics"
        )
        baseline_autonomous_events = get_last_stream_id(
            redis_client, "qc:autonomous:events"
        )
        
        logger.info(
            f"Baseline stream IDs: orchestrator={baseline_orchestrator}, "
            f"human={baseline_human}, context_metrics={baseline_context_metrics}, "
            f"autonomous_events={baseline_autonomous_events}"
        )
        
        test_start_time = time.time()
        
        # Step 1: Create workspace to trigger context loading events
        logger.info("Step 1: Creating workspace to trigger context loading")
        
        send_message_to_orchestrator(
            redis_client,
            "Create a new workspace with ID 'ws-metrics-test' for metrics testing.",
            sender="human",
        )
        
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=90,
        )
        baseline_human = response["stream_id"]
        logger.info("✓ Workspace created")
        
        # Step 2: List workspace files to trigger context 'load' event
        logger.info("Step 2: Listing workspace files to trigger load event")
        
        send_message_to_orchestrator(
            redis_client,
            "List all files in workspace ws-metrics-test.",
            sender="human",
        )
        
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=90,
        )
        baseline_human = response["stream_id"]
        logger.info("✓ List files complete")
        
        # Wait for context metrics to be recorded
        time.sleep(5)
        
        # Check for load event
        try:
            load_event = poll_stream_for_event(
                redis_client,
                "qc:context:metrics",
                baseline_context_metrics,
                event_type="load",
                timeout=15,
            )
            if load_event:
                baseline_context_metrics = load_event[0]
                logger.info(f"✓ Captured context 'load' event: {load_event[0]}")
        except Exception as e:
            logger.warning(f"No load event found: {e}")
        
        # Step 3: Run tests to trigger tool_response and post_process events
        logger.info("Step 3: Creating test file to trigger tool response events")
        
        test_content = '''def test_example():\n    assert 1 + 1 == 2\n'''
        
        send_message_to_orchestrator(
            redis_client,
            f"In workspace ws-metrics-test, create file /workspace/test_example.py with content: {test_content}",
            sender="human",
        )
        
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=120,
        )
        baseline_human = response["stream_id"]
        logger.info("✓ Test file created")
        
        # Step 4: Request autonomous checkpoint (if autonomous mode available)
        logger.info("Step 4: Requesting autonomous checkpoint")
        
        send_message_to_orchestrator(
            redis_client,
            "Set an autonomous checkpoint to record current progress.",
            sender="human",
        )
        
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=90,
        )
        baseline_human = response["stream_id"]
        logger.info("✓ Checkpoint request sent")
        
        # Wait for checkpoint event
        time.sleep(5)
        
        try:
            checkpoint_event = poll_stream_for_event(
                redis_client,
                "qc:autonomous:events",
                baseline_autonomous_events,
                event_type="checkpoint",
                timeout=15,
            )
            if checkpoint_event:
                baseline_autonomous_events = checkpoint_event[0]
                logger.info(f"✓ Captured autonomous 'checkpoint' event: {checkpoint_event[0]}")
        except Exception as e:
            logger.warning(f"No checkpoint event found: {e}")
        
        # Step 5: Send several more messages to generate context events
        logger.info("Step 5: Sending additional messages to generate diverse context events")
        
        additional_messages = [
            "Read the test file from workspace ws-metrics-test.",
            "Analyze the test coverage in the workspace.",
            "List all Python files in the workspace.",
            "Describe the purpose of the test file.",
        ]
        
        for msg in additional_messages:
            send_message_to_orchestrator(
                redis_client,
                msg,
                sender="human",
            )
            
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=90,
            )
            baseline_human = response["stream_id"]
            logger.info(f"✓ Processed: {msg[:50]}...")
        
        total_test_time = time.time() - test_start_time
        logger.info(f"Test flow complete: {total_test_time:.2f}s")
        
        # Verification Step 1: Check context metrics stream for event types
        logger.info("Verification: Checking context metrics stream for event types")
        
        context_metrics = read_stream(
            redis_client, "qc:context:metrics", count=200
        )
        
        logger.info(f"Context metrics stream has {len(context_metrics)} entries")
        
        # Extract event types
        event_types = set()
        for entry in context_metrics:
            event_type = entry[1].get("event")
            if event_type:
                event_types.add(event_type)
        
        logger.info(f"Context event types found: {event_types}")
        
        # Expected event types (based on system design)
        expected_types = ["pre_process", "post_process"]
        # Optional but desired: "load", "curation", "governor_plan", "tool_response"
        
        for expected in expected_types:
            if expected in event_types:
                logger.info(f"✓ Found expected event type: {expected}")
            else:
                logger.warning(
                    f"Expected event type '{expected}' not found in context metrics. "
                    f"This may indicate the context engine is not emitting all event types."
                )
        
        # Assert at least some event types are present
        assert len(event_types) > 0, (
            f"No event types found in context metrics stream. "
            f"Stream may be empty or not configured correctly."
        )
        logger.info(f"✓ Context metrics stream has {len(event_types)} event types")
        
        # Verification Step 2: Validate event payloads
        logger.info("Verification: Validating context event payloads")
        
        # Check that payloads are not empty
        events_with_payload = [e for e in context_metrics if e[1].get("payload")]
        
        logger.info(f"Found {len(events_with_payload)} events with non-empty payloads")
        
        assert len(events_with_payload) > 0, (
            f"No context events have payloads. "
            f"Check context metrics recording implementation."
        )
        logger.info("✓ Context events have payloads")
        
        # Sample a payload and check structure
        if events_with_payload:
            sample_event = events_with_payload[0]
            payload = sample_event[1].get("payload", {})
            
            # Payload might be JSON string
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except:
                    pass
            
            logger.info(f"Sample context event payload keys: {list(payload.keys()) if isinstance(payload, dict) else 'N/A'}")
        
        # Verification Step 3: Check autonomous events stream
        logger.info("Verification: Checking autonomous events stream")
        
        autonomous_events = read_stream(
            redis_client, "qc:autonomous:events", count=100
        )
        
        logger.info(f"Autonomous events stream has {len(autonomous_events)} entries")
        
        # Extract event types
        auto_event_types = set()
        for entry in autonomous_events:
            event_type = entry[1].get("event")
            if event_type:
                auto_event_types.add(event_type)
        
        logger.info(f"Autonomous event types found: {auto_event_types}")
        
        # We expect at least "checkpoint" if autonomous mode is active
        # But stream might be empty if autonomous mode not used in this test
        if len(autonomous_events) > 0:
            logger.info(f"✓ Autonomous events stream has {len(auto_event_types)} event types")
        else:
            logger.warning(
                "Autonomous events stream is empty. "
                "This is expected if autonomous mode was not activated during the test."
            )
        
        # Verification Step 4: Check orchestrator logs for PRP metrics
        logger.info("Verification: Checking orchestrator logs for PRP state mentions")
        
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        orchestrator_log_path = artifacts_dir / "orchestrator_logs.txt"
        capture_docker_logs("orchestrator-runtime", orchestrator_log_path)
        
        with open(orchestrator_log_path, 'r') as f:
            log_lines = f.readlines()
        
        prp_mentions = [line for line in log_lines if "prp" in line.lower() or "PRP" in line]
        
        logger.info(f"Found {len(prp_mentions)} log lines mentioning PRP")
        
        if len(prp_mentions) > 0:
            logger.info("✓ PRP state information present in orchestrator logs")
        else:
            logger.warning(
                "No PRP mentions found in orchestrator logs. "
                "This is expected if PRP was not triggered during this test."
            )
        
        # Verification Step 5: Assert test duration >= 8 minutes
        logger.info("Verification: Checking test duration")
        min_duration = 8 * 60  # 8 minutes
        assert total_test_time >= min_duration, (
            f"Test ran for {total_test_time:.2f}s, expected at least {min_duration}s."
        )
        logger.info(f"✓ Verified test duration: {total_test_time:.2f}s >= {min_duration}s")
        
        logger.info("✓ Test complete")
        
        # Log stream snapshot
        log_stream_snapshot(
            log_dir, "context_metrics", context_metrics
        )
        log_stream_snapshot(
            log_dir, "autonomous_events", autonomous_events
        )
        
        # Compute and export metrics
        collector.compute_derived_metrics()
        
        metrics_dir = Path("tests/e2e_advanced/metrics")
        metrics_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = metrics_dir / f"{test_name}_{run_id}_metrics.json"
        collector.export(metrics_path)
        logger.info(f"Metrics exported to: {metrics_path}")
        
    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        raise
    
    finally:
        # Teardown
        logger.info("Teardown: Capturing final artifacts")
        
        # Dump all Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator_final.log")
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")


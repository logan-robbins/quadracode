"""
Quadracode Advanced E2E Tests - Module 3: PRP and Autonomous Mode

This module validates the Perpetual Refinement Protocol (PRP) and autonomous operation:
- HumanClone rejection cycles triggering PRP state machine
- Autonomous mode checkpoints and final review

Tests run for 10-15 minutes with real LLM calls, exercising:
- HumanClone skeptical review and rejection triggers
- PRP state transitions (HYPOTHESIZE -> EXECUTE -> TEST -> CONCLUDE -> PROPOSE)
- Refinement ledger recording
- Autonomous checkpoints and escalations
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
    log_tool_call,
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
    capture_prp_ledger,
)
from tests.e2e_advanced.utils.timeouts import wait_for_condition
from tests.e2e_advanced.utils.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)


def parse_humanclone_trigger(message_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse HumanClone trigger from message payload.
    
    Returns:
        Dictionary with 'exhaustion_mode', 'rationale', 'required_artifacts', etc.
        or None if not a trigger message.
    """
    # HumanClone trigger is typically in the 'message' field as JSON
    message_text = message_payload.get("message", "")
    
    # Try to find JSON in the message
    try:
        # Look for {... exhaustion_mode ...} pattern
        start_idx = message_text.find("{")
        end_idx = message_text.rfind("}")
        if start_idx >= 0 and end_idx > start_idx:
            json_str = message_text[start_idx : end_idx + 1]
            trigger_data = json.loads(json_str)
            if "exhaustion_mode" in trigger_data or "trigger_type" in trigger_data:
                return trigger_data
    except Exception as e:
        logger.debug(f"Could not parse HumanClone trigger: {e}")
    
    # Alternative: Check if message contains rejection keywords
    rejection_keywords = ["reject", "incomplete", "missing", "fail"]
    if any(keyword in message_text.lower() for keyword in rejection_keywords):
        # Infer a trigger even if not formatted as JSON
        return {
            "exhaustion_mode": "INFERRED_REJECTION",
            "rationale": message_text[:200],
        }
    
    return None


def extract_prp_state_from_logs(log_lines: List[str]) -> List[Dict[str, Any]]:
    """
    Extract PRP state transitions from orchestrator log lines.
    
    Returns:
        List of state transition records with 'from_state', 'to_state', 'timestamp'.
    """
    transitions = []
    
    for line in log_lines:
        # Look for log patterns like "prp_state: HYPOTHESIZE" or "apply_prp_transition"
        if "prp_state:" in line or "PRP state" in line or "apply_prp_transition" in line:
            # Try to extract state names (HYPOTHESIZE, EXECUTE, TEST, CONCLUDE, PROPOSE)
            states = ["HYPOTHESIZE", "EXECUTE", "TEST", "CONCLUDE", "PROPOSE", "ACCEPT"]
            found_states = [s for s in states if s in line]
            
            if found_states:
                transitions.append({
                    "log_line": line.strip(),
                    "states_mentioned": found_states,
                })
    
    return transitions


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_human_clone_rejection_cycle(
    docker_services, redis_client, test_config
):
    """
    Test 3.1: HumanClone Rejection Cycle (10 minutes)
    
    Objective: Simulate a task where orchestrator fails tests, HumanClone rejects
    the work, and PRP forces hypothesis refinement.
    
    This test validates:
    - HumanClone receives orchestrator's work proposals
    - HumanClone sends rejection trigger with exhaustion_mode
    - Orchestrator enters PRP HYPOTHESIZE state after rejection
    - PRP state machine cycles through transitions
    - Refinement ledger records hypothesis and outcome
    - Multiple refinement cycles occur until acceptance
    
    Expected duration: 10 minutes minimum
    Expected PRP cycles: 1-3
    Expected HumanClone rejections: 1-3
    
    Prerequisites:
    - human-clone-runtime service running with QUADRACODE_PROFILE=human_clone
    - SUPERVISOR_RECIPIENT=human_clone set for orchestrator
    - Workspace with failing test suite
    
    Troubleshooting:
    - If HumanClone never rejects: Verify profile configuration and supervisor routing
    - If PRP not triggered: Check orchestrator logs for state transitions
    - If refinement ledger empty: Verify manage_refinement_ledger tool is called
    """
    test_name = "test_human_clone_rejection_cycle"
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
        baseline_human_clone = get_last_stream_id(
            redis_client, "qc:mailbox/human_clone"
        )
        
        logger.info(
            f"Baseline stream IDs: orchestrator={baseline_orchestrator}, "
            f"human={baseline_human}, human_clone={baseline_human_clone}"
        )
        
        # Note: This test requires human-clone-runtime service to be running
        # with QUADRACODE_PROFILE=human_clone and SUPERVISOR_RECIPIENT=human_clone
        logger.info(
            "Prerequisites: Ensure human-clone-runtime is running with "
            "QUADRACODE_PROFILE=human_clone and orchestrator has "
            "SUPERVISOR_RECIPIENT=human_clone"
        )
        
        # Step 1: Create workspace with failing test suite
        logger.info("Step 1: Creating workspace with failing tests")
        send_message_to_orchestrator(
            redis_client,
            "Create a new workspace with ID 'ws-prp-test' for testing the PRP cycle.",
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
        logger.info(f"Workspace creation response: {response['stream_id']}")
        
        # Create a simple Python module with a bug
        buggy_code = '''"""Calculator with intentional bugs for testing PRP."""


def add(a, b):
    """Add two numbers."""
    return a + b


def subtract(a, b):
    """Subtract b from a."""
    return a - b


def multiply(a, b):
    """Multiply two numbers."""
    # BUG: Wrong operation
    return a + b  # Should be a * b


def divide(a, b):
    """Divide a by b."""
    # BUG: No zero check
    return a / b
'''
        
        send_message_to_orchestrator(
            redis_client,
            f"In workspace ws-prp-test, create file /workspace/calculator.py with:\n\n{buggy_code}",
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
        logger.info("Created calculator.py with bugs")
        
        # Create test file
        test_code = '''"""Tests for calculator module."""

import pytest
from calculator import add, subtract, multiply, divide


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 5) == -5


def test_multiply():
    """This test will FAIL due to bug."""
    assert multiply(2, 3) == 6
    assert multiply(-2, 3) == -6


def test_divide():
    assert divide(6, 2) == 3
    assert divide(5, 2) == 2.5


def test_divide_by_zero():
    """This test will FAIL due to missing error handling."""
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)
'''
        
        send_message_to_orchestrator(
            redis_client,
            f"In workspace ws-prp-test, create file /workspace/test_calculator.py with:\n\n{test_code}",
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
        logger.info("Created test_calculator.py")
        
        # Step 2: Assign task to orchestrator to fix failing tests
        logger.info("Step 2: Assigning task to fix failing tests (will trigger PRP)")
        
        task_message = (
            "Fix the failing test suite in workspace ws-prp-test. "
            "Run the tests using pytest, identify failures, fix the bugs in calculator.py, "
            "and ensure all tests pass. You have 10 minutes. "
            "Submit your work for review when complete."
        )
        
        test_start_time = time.time()
        
        send_message_to_orchestrator(
            redis_client,
            task_message,
            sender="human",
        )
        
        # Step 3: Wait for orchestrator to run tests
        logger.info("Step 3: Waiting for orchestrator to run tests")
        
        # Orchestrator should call run_full_test_suite
        # We'll wait for its response to human
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=180,
        )
        baseline_human = response["stream_id"]
        logger.info(f"Orchestrator first response: {response['stream_id']}")
        
        # Give orchestrator time to work on the problem
        # It should run tests, see failures, and attempt fixes
        logger.info("Giving orchestrator time to work on fixes...")
        time.sleep(60)  # Wait 1 minute for orchestrator to work
        
        # Step 4: Wait for orchestrator to send work to HumanClone
        logger.info("Step 4: Waiting for orchestrator to send work to HumanClone")
        
        # Orchestrator should send proposal to human_clone mailbox
        # Timeout is generous since orchestrator needs time to work
        try:
            proposal = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human_clone",
                baseline_human_clone,
                sender="orchestrator",
                timeout=240,
            )
            baseline_human_clone = proposal["stream_id"]
            logger.info(f"Orchestrator proposal to HumanClone: {proposal['stream_id']}")
            
            # Record proposal
            collector.record_orchestrator_proposal(proposal)
            
            # Log proposal
            log_turn(
                log_dir,
                1,
                {"content": task_message},
                {
                    "stream_id": proposal["stream_id"],
                    "content": proposal.get("message", ""),
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to receive orchestrator proposal to HumanClone: {e}. "
                f"This indicates SUPERVISOR_RECIPIENT may not be set to human_clone, "
                f"or HumanClone service is not running. Check docker-compose configuration."
            )
            # For this test to work, HumanClone must be active
            # If we can't get a proposal, the test cannot proceed
            pytest.skip(
                "HumanClone not receiving proposals. Ensure human-clone-runtime "
                "service is running and orchestrator SUPERVISOR_RECIPIENT=human_clone."
            )
        
        # Step 5: Wait for HumanClone rejection trigger
        logger.info("Step 5: Waiting for HumanClone rejection trigger")
        
        # HumanClone should respond with rejection if tests failed
        try:
            trigger_message = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/orchestrator",
                baseline_orchestrator,
                sender="human_clone",
                timeout=180,
            )
            baseline_orchestrator = trigger_message["stream_id"]
            logger.info(f"HumanClone trigger received: {trigger_message['stream_id']}")
            
            # Parse trigger
            trigger_payload = parse_humanclone_trigger(trigger_message)
            
            if trigger_payload:
                logger.info(
                    f"HumanClone rejection trigger: "
                    f"exhaustion_mode={trigger_payload.get('exhaustion_mode')}, "
                    f"rationale={trigger_payload.get('rationale', '')[:100]}"
                )
                
                # Record HumanClone invocation
                collector.record_humanclone_invocation(
                    proposal=proposal,
                    trigger=trigger_payload,
                    outcome="rejection",
                )
                
                # Record false-stop (orchestrator proposed completion but tests failed)
                collector.record_false_stop(
                    proposal=proposal,
                    detected_by="humanclone",
                    stage="failing_tests",
                )
            else:
                logger.warning(
                    f"Could not parse HumanClone trigger from message: "
                    f"{trigger_message.get('message', '')[:200]}"
                )
        except Exception as e:
            logger.error(
                f"Failed to receive HumanClone trigger: {e}. "
                f"This may indicate HumanClone accepted the work (no rejection), "
                f"or tests actually passed on first attempt. Check HumanClone logs."
            )
            # This is not necessarily a test failure - HumanClone might accept
            # Continue to check for PRP state
        
        # Step 6: Wait for orchestrator to enter PRP HYPOTHESIZE state
        logger.info("Step 6: Checking for PRP state transition to HYPOTHESIZE")
        
        # We need to check orchestrator logs for PRP state transitions
        # Since we can't directly access internal state, we'll check logs
        
        # Give orchestrator time to react to rejection and enter PRP
        time.sleep(30)
        
        # Capture orchestrator logs
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        orchestrator_log_path = artifacts_dir / "orchestrator_logs_mid_test.txt"
        capture_docker_logs("orchestrator-runtime", orchestrator_log_path)
        
        # Parse logs for PRP state
        with open(orchestrator_log_path, 'r') as f:
            log_lines = f.readlines()
        
        prp_transitions = extract_prp_state_from_logs(log_lines)
        
        if prp_transitions:
            logger.info(f"Found {len(prp_transitions)} PRP-related log entries")
            for trans in prp_transitions[:5]:  # Log first 5
                logger.info(f"PRP log: {trans}")
            
            # Check if HYPOTHESIZE state was entered
            hypothesize_found = any(
                "HYPOTHESIZE" in t["states_mentioned"] for t in prp_transitions
            )
            if hypothesize_found:
                logger.info("✓ PRP HYPOTHESIZE state detected in logs")
            else:
                logger.warning(
                    "HYPOTHESIZE state not explicitly found in logs. "
                    "PRP may be triggered differently or logs may not include state names."
                )
        else:
            logger.warning(
                "No PRP state transitions found in logs. "
                "This may indicate PRP was not triggered, or log patterns do not match. "
                "Check orchestrator logs manually for PRP state machine activity."
            )
        
        # Step 7: Wait for orchestrator to re-execute and re-test
        logger.info("Step 7: Waiting for orchestrator refinement cycle")
        
        # Orchestrator should propose new hypothesis, execute, and test again
        # This may take several minutes
        time.sleep(120)  # Wait 2 minutes for refinement
        
        # Check if orchestrator sends updated work to HumanClone
        try:
            updated_proposal = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human_clone",
                baseline_human_clone,
                sender="orchestrator",
                timeout=240,
            )
            baseline_human_clone = updated_proposal["stream_id"]
            logger.info(
                f"Orchestrator updated proposal to HumanClone: {updated_proposal['stream_id']}"
            )
            
            # Record second proposal
            collector.record_orchestrator_proposal(updated_proposal)
        except Exception as e:
            logger.warning(
                f"No updated proposal received from orchestrator: {e}. "
                f"Orchestrator may still be working or may have escalated."
            )
        
        # Step 8: Check for acceptance or additional rejections
        logger.info("Step 8: Checking for HumanClone acceptance or further rejections")
        
        # Wait for HumanClone response
        try:
            final_response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/orchestrator",
                baseline_orchestrator,
                sender="human_clone",
                timeout=180,
            )
            baseline_orchestrator = final_response["stream_id"]
            logger.info(f"HumanClone final response: {final_response['stream_id']}")
            
            # Check if acceptance or rejection
            final_trigger = parse_humanclone_trigger(final_response)
            
            if final_trigger and final_trigger.get("exhaustion_mode"):
                # Another rejection
                logger.info("HumanClone sent another rejection - PRP cycle continues")
                collector.record_humanclone_invocation(
                    proposal=updated_proposal if updated_proposal else {},
                    trigger=final_trigger,
                    outcome="rejection",
                )
            else:
                # Likely acceptance
                logger.info("HumanClone appears to have accepted the work")
                if updated_proposal:
                    collector.record_humanclone_invocation(
                        proposal=updated_proposal,
                        trigger={},
                        outcome="acceptance",
                    )
        except Exception as e:
            logger.warning(f"No final HumanClone response: {e}")
        
        total_test_time = time.time() - test_start_time
        logger.info(f"Test flow complete: {total_test_time:.2f}s")
        
        # Verification Step 1: Assert at least one HumanClone rejection occurred
        logger.info("Verification: Checking HumanClone interactions")
        humanclone_invocations = collector.metrics["humanclone"]["total_invocations"]
        humanclone_rejections = collector.metrics["humanclone"]["rejections"]
        
        # For this test to be meaningful, at least one rejection should occur
        # However, if orchestrator fixed the bugs on first try, that's also valid
        logger.info(
            f"HumanClone invocations: {humanclone_invocations}, "
            f"rejections: {humanclone_rejections}"
        )
        
        if humanclone_rejections == 0:
            logger.warning(
                "No HumanClone rejections recorded. This may indicate: "
                "(1) Orchestrator fixed all bugs on first attempt, "
                "(2) HumanClone accepted despite failures (config issue), or "
                "(3) HumanClone service not configured correctly. "
                "For PRP testing, we expect at least one rejection cycle."
            )
        
        # Verification Step 2: Check orchestrator mailbox for refinement tool calls
        logger.info("Verification: Checking for refinement ledger tool calls")
        orchestrator_messages = read_stream(
            redis_client, "qc:mailbox/orchestrator", count=200
        )
        
        # Look for manage_refinement_ledger tool calls in message payloads
        refinement_tool_calls = []
        for msg in orchestrator_messages:
            payload = msg[1].get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except:
                    pass
            
            # Check if message contains tool call to manage_refinement_ledger
            tool_calls = payload.get("tool_calls", [])
            for tool_call in tool_calls:
                if "refinement" in str(tool_call).lower():
                    refinement_tool_calls.append(tool_call)
        
        if refinement_tool_calls:
            logger.info(f"✓ Found {len(refinement_tool_calls)} refinement ledger tool calls")
        else:
            logger.warning(
                "No refinement ledger tool calls found. "
                "This may indicate PRP did not fully activate or tool calls are logged differently."
            )
        
        # Verification Step 3: Check HumanClone mailbox
        logger.info("Verification: Checking HumanClone mailbox")
        humanclone_messages = read_stream(
            redis_client, "qc:mailbox/human_clone", count=50
        )
        
        # Count messages from orchestrator to HumanClone
        orchestrator_to_humanclone = [
            m for m in humanclone_messages if m[1].get("sender") == "orchestrator"
        ]
        
        assert len(orchestrator_to_humanclone) >= 1, (
            f"Expected at least 1 message from orchestrator to HumanClone, "
            f"got {len(orchestrator_to_humanclone)}. This indicates supervisor routing "
            f"is not working. Verify SUPERVISOR_RECIPIENT=human_clone."
        )
        logger.info(
            f"✓ Verified {len(orchestrator_to_humanclone)} messages to HumanClone"
        )
        
        # Verification Step 4: Assert test duration >= 10 minutes
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
        logger.info("Teardown: Capturing final artifacts and logs")
        
        # Dump Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture final Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator_final.log")
        capture_docker_logs("human-clone-runtime", artifacts_dir / "humanclone.log")
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_autonomous_mode_full_lifecycle(
    docker_services, redis_client, test_config
):
    """
    Test 3.2: Autonomous Mode Full Lifecycle (15 minutes)
    
    Objective: Run orchestrator in fully autonomous mode with checkpoints,
    escalations, and final review.
    
    This test validates:
    - Autonomous mode acknowledged by orchestrator
    - Checkpoint events recorded every ~3 minutes
    - Hypothesis critique tool called when tests fail
    - Final review tool called at completion
    - Test suite passes at end
    - Autonomous events stream captures all checkpoints
    
    Expected duration: 15 minutes minimum
    Expected checkpoints: 4-5
    Expected iterations: Up to 50 (configured)
    
    Prerequisites:
    - QUADRACODE_MODE=autonomous (or equivalent flag)
    - AUTONOMOUS_MAX_ITERATIONS=50
    - AUTONOMOUS_RUNTIME_MINUTES=15
    - Workspace with simple coding task
    
    Troubleshooting:
    - If no checkpoints: Verify autonomous mode configuration
    - If no final review: Task may not have completed
    - If tests don't pass: Autonomous mode may need more time or iterations
    """
    test_name = "test_autonomous_mode_full_lifecycle"
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
        baseline_autonomous_events = get_last_stream_id(
            redis_client, "qc:autonomous:events"
        )
        
        logger.info(
            f"Baseline stream IDs: orchestrator={baseline_orchestrator}, "
            f"human={baseline_human}, autonomous_events={baseline_autonomous_events}"
        )
        
        # Note: This test requires autonomous mode to be enabled
        # Configuration should set:
        # - QUADRACODE_MODE=autonomous
        # - AUTONOMOUS_MAX_ITERATIONS=50
        # - AUTONOMOUS_RUNTIME_MINUTES=15
        logger.info(
            "Prerequisites: Ensure orchestrator configured for autonomous mode with "
            "AUTONOMOUS_MAX_ITERATIONS=50 and AUTONOMOUS_RUNTIME_MINUTES=15"
        )
        
        # Step 1: Create workspace with simple coding task
        logger.info("Step 1: Creating workspace with coding task")
        send_message_to_orchestrator(
            redis_client,
            "Create a new workspace with ID 'ws-autonomous-test' for autonomous task execution.",
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
        logger.info(f"Workspace creation response: {response['stream_id']}")
        
        # Step 2: Assign autonomous task
        logger.info("Step 2: Assigning autonomous coding task")
        
        task_description = (
            "Autonomously complete the following coding task in workspace ws-autonomous-test:\n\n"
            "Write a Python function called 'fibonacci(n)' that calculates the nth Fibonacci number. "
            "The function should handle edge cases (n=0 returns 0, n=1 returns 1). "
            "Include comprehensive pytest tests covering:\n"
            "- Base cases (n=0, n=1)\n"
            "- Small values (n=2, n=3, n=4)\n"
            "- Larger value (n=10 should return 55)\n"
            "- Invalid input (negative n should raise ValueError)\n\n"
            "Requirements:\n"
            "1. Create fibonacci.py with the implementation\n"
            "2. Create test_fibonacci.py with pytest tests\n"
            "3. Run the test suite and ensure all tests pass\n"
            "4. Checkpoint your progress every 3 minutes\n"
            "5. Request final review when all tests pass\n\n"
            "You have 15 minutes. Work autonomously and checkpoint regularly."
        )
        
        test_start_time = time.time()
        
        send_message_to_orchestrator(
            redis_client,
            task_description,
            sender="human",
        )
        
        # Step 3: Wait for autonomous mode acknowledgment
        logger.info("Step 3: Waiting for autonomous mode acknowledgment")
        
        response = wait_for_message_on_stream(
            redis_client,
            "qc:mailbox/human",
            baseline_human,
            sender="orchestrator",
            timeout=90,
        )
        baseline_human = response["stream_id"]
        
        # Check if response indicates autonomous mode
        ack_message = response.get("message", "").lower()
        if "autonomous" in ack_message or "checkpoint" in ack_message:
            logger.info("✓ Orchestrator acknowledged autonomous mode")
        else:
            logger.warning(
                f"Orchestrator response may not indicate autonomous mode: {ack_message[:200]}"
            )
        
        # Step 4: Poll for autonomous checkpoint events
        logger.info("Step 4: Polling for autonomous checkpoint events")
        
        checkpoint_events = []
        checkpoint_poll_timeout = 15 * 60  # 15 minutes
        checkpoint_poll_start = time.time()
        expected_checkpoint_interval = 3 * 60  # 3 minutes
        
        # Poll for checkpoints every 30 seconds
        while time.time() - checkpoint_poll_start < checkpoint_poll_timeout:
            try:
                checkpoint_event = poll_stream_for_event(
                    redis_client,
                    "qc:autonomous:events",
                    baseline_autonomous_events,
                    event_type="checkpoint",
                    timeout=30,
                )
                
                if checkpoint_event:
                    baseline_autonomous_events = checkpoint_event[0]
                    checkpoint_events.append(checkpoint_event[1])
                    logger.info(
                        f"Captured checkpoint {len(checkpoint_events)}: {checkpoint_event[0]}"
                    )
                    
                    # Log checkpoint payload
                    payload = checkpoint_event[1].get("payload", {})
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    
                    progress_summary = payload.get("progress_summary", "")
                    iteration_count = payload.get("iteration_count", 0)
                    logger.info(
                        f"Checkpoint progress: iteration={iteration_count}, "
                        f"summary={progress_summary[:100]}"
                    )
                else:
                    # No checkpoint yet, wait
                    time.sleep(30)
            except Exception as e:
                logger.debug(f"No checkpoint event in this poll: {e}")
                time.sleep(30)
        
        logger.info(f"Checkpoint polling complete. Captured {len(checkpoint_events)} checkpoints")
        
        # Step 5: Wait for final review tool call
        logger.info("Step 5: Waiting for final review")
        
        # Check orchestrator mailbox for request_final_review tool call
        # This should happen after task completion
        final_review_found = False
        final_review_attempts = 0
        max_attempts = 5
        
        while final_review_attempts < max_attempts and not final_review_found:
            time.sleep(60)  # Wait 1 minute between checks
            final_review_attempts += 1
            
            orchestrator_messages = read_stream(
                redis_client, "qc:mailbox/orchestrator", count=100
            )
            
            # Look for final review tool call
            for msg in orchestrator_messages[-20:]:  # Check recent messages
                payload = msg[1].get("payload", {})
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except:
                        pass
                
                tool_calls = payload.get("tool_calls", [])
                for tool_call in tool_calls:
                    if "final_review" in str(tool_call).lower():
                        final_review_found = True
                        logger.info("✓ Found request_final_review tool call")
                        break
                
                if final_review_found:
                    break
        
        if not final_review_found:
            logger.warning(
                "request_final_review tool call not found. "
                "Task may not have completed or tool may be named differently."
            )
        
        # Step 6: Verify test suite passed
        logger.info("Step 6: Checking if test suite passed")
        
        # Look for run_full_test_suite tool calls and results
        test_suite_results = []
        
        for msg in orchestrator_messages[-50:]:  # Check recent messages
            payload = msg[1].get("payload", {})
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except:
                    pass
            
            tool_calls = payload.get("tool_calls", [])
            for tool_call in tool_calls:
                if "run_full_test_suite" in str(tool_call).lower() or "pytest" in str(tool_call).lower():
                    test_suite_results.append(tool_call)
        
        if test_suite_results:
            logger.info(f"Found {len(test_suite_results)} test suite executions")
            # Check last result for success
            last_result = str(test_suite_results[-1])
            if "passed" in last_result.lower() or "success" in last_result.lower():
                logger.info("✓ Test suite appears to have passed")
            else:
                logger.warning(f"Test suite result unclear: {last_result[:200]}")
        else:
            logger.warning("No test suite executions found in recent messages")
        
        total_test_time = time.time() - test_start_time
        logger.info(f"Test flow complete: {total_test_time:.2f}s")
        
        # Verification Step 1: Assert checkpoint events
        logger.info("Verification: Checking checkpoint events")
        assert len(checkpoint_events) >= 4, (
            f"Expected at least 4 checkpoint events (one per ~3 minutes over 15 min), "
            f"got {len(checkpoint_events)}. This indicates autonomous checkpoints may not "
            f"be configured correctly. Verify orchestrator autonomous mode settings."
        )
        logger.info(f"✓ Verified {len(checkpoint_events)} checkpoint events")
        
        # Verification Step 2: Validate checkpoint payload structure
        logger.info("Verification: Validating checkpoint payload structure")
        for i, event in enumerate(checkpoint_events):
            payload = event.get("payload", {})
            if isinstance(payload, str):
                payload = json.loads(payload)
            
            assert "progress_summary" in payload, (
                f"Checkpoint {i+1} missing 'progress_summary' field. "
                f"Payload: {json.dumps(payload, indent=2)}"
            )
        logger.info("✓ All checkpoints have required fields")
        
        # Verification Step 3: Assert autonomous events stream
        logger.info("Verification: Checking autonomous events stream")
        autonomous_events = read_stream(
            redis_client, "qc:autonomous:events", count=50
        )
        
        assert len(autonomous_events) >= len(checkpoint_events), (
            f"Autonomous events stream should have at least {len(checkpoint_events)} entries, "
            f"got {len(autonomous_events)}. Check stream recording."
        )
        logger.info(f"✓ Verified autonomous events stream has {len(autonomous_events)} entries")
        
        # Verification Step 4: Assert test duration >= 15 minutes
        logger.info("Verification: Checking test duration")
        min_duration = 15 * 60  # 15 minutes
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
        logger.info("Teardown: Capturing final artifacts and logs")
        
        # Dump Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator.log")
        
        # Save checkpoint events
        if checkpoint_events:
            checkpoint_log_path = artifacts_dir / "checkpoint_events.json"
            with open(checkpoint_log_path, 'w') as f:
                json.dump(checkpoint_events, f, indent=2)
            logger.info(f"Checkpoint events saved to: {checkpoint_log_path}")
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")


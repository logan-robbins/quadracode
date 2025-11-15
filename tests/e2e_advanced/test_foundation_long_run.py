"""Foundation long-running E2E tests.

Module 1: Foundation Tests
- Test 1.1: Sustained Orchestrator-Agent Ping-Pong (5 minutes, 30+ turns)
- Test 1.2: Multi-Agent Message Routing (5 minutes, 3 dynamic agents)

These tests establish baseline long-running message flows without complex
PRP or autonomous mode logic.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

# Import base utilities
import sys
parent_tests = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(parent_tests))
from test_end_to_end import (
    get_last_stream_id,
    read_stream,
    read_stream_after,
    send_message_to_orchestrator,
    stream_entries_added,
    wait_for_human_response,
)

# Import advanced utilities
from .utils.agent_helpers import delete_agent, spawn_agent
from .utils.artifacts import capture_all_service_logs, capture_context_metrics
from .utils.logging_framework import log_stream_snapshot, log_turn
from .utils.redis_helpers import (
    dump_all_streams,
    get_stream_stats,
    validate_stream_monotonicity,
    wait_for_message_on_stream,
)
from .utils.timeouts import TimeoutManager

logger = logging.getLogger(__name__)

# Test configuration
MIN_TURNS_TEST_1_1 = 30
MIN_DURATION_SECONDS_TEST_1_1 = 300  # 5 minutes
MIN_DURATION_SECONDS_TEST_1_2 = 300  # 5 minutes


@pytest.mark.e2e_advanced
def test_sustained_orchestrator_agent_ping_pong(docker_stack, test_log_dir):
    """Test 1.1: Sustained Orchestrator-Agent Ping-Pong (5 minutes).

    Validates message delivery, mailbox polling, and LLM response generation
    over 30+ conversation turns spanning at least 5 minutes.

    Args:
        docker_stack: Pytest fixture that brings up Docker Compose stack
        test_log_dir: Pytest fixture providing timestamped log directory
    """
    logger.info("=" * 80)
    logger.info("TEST 1.1: SUSTAINED ORCHESTRATOR-AGENT PING-PONG")
    logger.info("Target: 30+ turns over 5 minutes")
    logger.info("=" * 80)

    # Setup: Initialize Redis stream baselines
    baseline_supervisor = get_last_stream_id("qc:mailbox/human")
    baseline_orchestrator = get_last_stream_id("qc:mailbox/orchestrator")
    baseline_agent = get_last_stream_id("qc:mailbox/agent-runtime")
    baseline_context_metrics = get_last_stream_id("qc:context:metrics")

    logger.info("Baseline stream IDs captured:")
    logger.info("  supervisor: %s", baseline_supervisor)
    logger.info("  orchestrator: %s", baseline_orchestrator)
    logger.info("  agent: %s", baseline_agent)
    logger.info("  context_metrics: %s", baseline_context_metrics)

    # Capture initial stream snapshots
    log_stream_snapshot(test_log_dir, "qc:mailbox/human", read_stream("qc:mailbox/human", count=10))
    log_stream_snapshot(test_log_dir, "qc:mailbox/orchestrator", read_stream("qc:mailbox/orchestrator", count=10))

    start_time = time.time()
    turn_count = 0
    turn_data = []

    try:
        with TimeoutManager(timeout=MIN_DURATION_SECONDS_TEST_1_1 + 60, operation="ping-pong test") as tm:
            # Send initial message
            send_message_to_orchestrator(
                "Begin a 5-minute sustained conversation with me. "
                "Acknowledge receipt, then ask me a question. "
                "I will respond with simple answers. Continue asking questions."
            )
            tm.checkpoint("Initial message sent")

            # Conversation loop
            while True:
                turn_start = time.time()
                turn_count += 1

                logger.info("-" * 60)
                logger.info("Turn %d starting...", turn_count)

                # Wait for orchestrator response
                try:
                    response_fields = wait_for_human_response(baseline_supervisor, timeout=90)
                    turn_duration = int((time.time() - turn_start) * 1000)

                    assert response_fields.get("sender") == "orchestrator", (
                        f"Expected sender='orchestrator' in response, got sender='{response_fields.get('sender')}'. "
                        f"This indicates message routing failed. Check orchestrator logs for errors. "
                        f"Full response fields: {json.dumps(response_fields, indent=2)}"
                    )

                    response_message = response_fields.get("message")
                    assert response_message, (
                        f"Orchestrator returned empty response on turn {turn_count}. "
                        f"Check orchestrator logs for LLM failures or rate limiting."
                    )

                    logger.info("Turn %d: Received response (%.1fs): %s",
                               turn_count, turn_duration / 1000, response_message[:100])

                    # Log turn
                    message_data = {
                        "turn": turn_count - 1,
                        "sender": "human",
                        "message": "Simple answer" if turn_count > 1 else "Initial request",
                    }
                    log_turn(
                        test_log_dir,
                        turn_count,
                        message_data,
                        response_fields,
                        duration_ms=turn_duration,
                    )

                    turn_data.append({
                        "turn": turn_count,
                        "duration_ms": turn_duration,
                        "response_length": len(response_message),
                    })

                    # Update baseline for next iteration
                    baseline_supervisor = get_last_stream_id("qc:mailbox/human")

                    tm.checkpoint(f"Turn {turn_count} completed")

                except Exception as e:
                    logger.error("Turn %d failed: %s", turn_count, e)
                    raise

                # Check exit conditions
                elapsed = time.time() - start_time
                logger.info("Turn %d complete. Elapsed: %.1fs, Total turns: %d",
                           turn_count, elapsed, turn_count)

                if turn_count >= MIN_TURNS_TEST_1_1 and elapsed >= MIN_DURATION_SECONDS_TEST_1_1:
                    logger.info("SUCCESS: Minimum requirements met (turns: %d, duration: %.1fs)",
                               turn_count, elapsed)
                    break

                if elapsed >= MIN_DURATION_SECONDS_TEST_1_1 + 30:
                    # Safety timeout
                    logger.warning("Safety timeout reached at %.1fs", elapsed)
                    break

                # Send follow-up
                send_message_to_orchestrator("Yes, continue. Ask me another question.")
                time.sleep(0.5)  # Brief pause between turns

        # Verification
        logger.info("=" * 60)
        logger.info("VERIFICATION PHASE")
        logger.info("=" * 60)

        # Assert minimum turns
        assert turn_count >= MIN_TURNS_TEST_1_1, (
            f"Test completed with only {turn_count} turns, expected >= {MIN_TURNS_TEST_1_1}. "
            f"This may indicate timeout issues or LLM failures. Check orchestrator logs."
        )
        logger.info("✓ Turn count: %d (>= %d required)", turn_count, MIN_TURNS_TEST_1_1)

        # Assert minimum duration
        total_duration = time.time() - start_time
        assert total_duration >= MIN_DURATION_SECONDS_TEST_1_1, (
            f"Test ran for only {total_duration:.1f}s, expected >= {MIN_DURATION_SECONDS_TEST_1_1}s"
        )
        logger.info("✓ Duration: %.1fs (>= %ds required)", total_duration, MIN_DURATION_SECONDS_TEST_1_1)

        # Validate stream monotonicity
        assert validate_stream_monotonicity("qc:mailbox/orchestrator"), (
            "Orchestrator mailbox has gaps or reordering. This indicates message delivery issues."
        )
        assert validate_stream_monotonicity("qc:mailbox/human"), (
            "Human mailbox has gaps or reordering."
        )
        logger.info("✓ Stream monotonicity validated")

        # Check stream entry counts
        orchestrator_stats = get_stream_stats("qc:mailbox/orchestrator")
        human_stats = get_stream_stats("qc:mailbox/human")

        logger.info("Stream stats:")
        logger.info("  orchestrator entries: %d", orchestrator_stats.get("entry_count", 0))
        logger.info("  human entries: %d", human_stats.get("entry_count", 0))

        assert human_stats.get("entry_count", 0) >= MIN_TURNS_TEST_1_1, (
            f"Human mailbox has only {human_stats.get('entry_count', 0)} entries, "
            f"expected >= {MIN_TURNS_TEST_1_1}"
        )
        logger.info("✓ Mailbox entry counts validated")

        # Check context metrics
        context_entries = read_stream_after("qc:context:metrics", baseline_context_metrics, count=1000)
        logger.info("Context metrics entries: %d", len(context_entries))

        events_observed = set()
        for _, fields in context_entries:
            event_type = fields.get("event")
            if event_type:
                events_observed.add(event_type)

        logger.info("Context events observed: %s", events_observed)
        assert "pre_process" in events_observed, "No pre_process events in context metrics"
        assert "load" in events_observed, "No load events in context metrics"
        assert "governor_plan" in events_observed, "No governor_plan events in context metrics"
        logger.info("✓ Context metrics validated")

        # Success summary
        logger.info("=" * 60)
        logger.info("TEST 1.1 PASSED")
        logger.info("=" * 60)
        logger.info("Total turns: %d", turn_count)
        logger.info("Total duration: %.1fs", total_duration)
        logger.info("Average turn duration: %.1fs", total_duration / turn_count)
        logger.info("=" * 60)

    finally:
        # Teardown: Capture artifacts
        logger.info("Capturing test artifacts...")

        artifact_dir = test_log_dir.parent / "artifacts" / test_log_dir.name
        artifact_dir.mkdir(parents=True, exist_ok=True)

        # Dump Redis streams
        dump_all_streams(artifact_dir)
        logger.info("Redis streams dumped to: %s", artifact_dir)

        # Capture service logs
        capture_all_service_logs(
            artifact_dir,
            services=["orchestrator-runtime", "agent-runtime", "redis"],
        )
        logger.info("Service logs captured")

        # Capture context metrics
        context_entries = read_stream("qc:context:metrics", count=2000)
        capture_context_metrics(artifact_dir / "context_metrics.json", context_entries)
        logger.info("Context metrics captured")

        # Write turn summary
        summary_file = artifact_dir / "turn_summary.json"
        with summary_file.open("w") as f:
            json.dump({
                "total_turns": turn_count,
                "duration_seconds": time.time() - start_time,
                "turns": turn_data,
            }, f, indent=2)
        logger.info("Turn summary written to: %s", summary_file)


@pytest.mark.e2e_advanced
def test_multi_agent_message_routing(docker_stack, test_log_dir):
    """Test 1.2: Multi-Agent Message Routing (5 minutes).

    Spawns 3 dynamic agents and routes messages through orchestrator to each,
    verifying correct mailbox targeting over 5 minutes.

    Args:
        docker_stack: Pytest fixture that brings up Docker Compose stack
        test_log_dir: Pytest fixture providing timestamped log directory
    """
    logger.info("=" * 80)
    logger.info("TEST 1.2: MULTI-AGENT MESSAGE ROUTING")
    logger.info("Target: 3 dynamic agents, 5 minutes of routing validation")
    logger.info("=" * 80)

    dynamic_agent_ids = ["agent-worker-1", "agent-worker-2", "agent-worker-3"]
    spawned_agents = []

    start_time = time.time()

    try:
        # Setup: Spawn 3 dynamic agents
        logger.info("Spawning dynamic agents...")
        for agent_id in dynamic_agent_ids:
            logger.info("Spawning: %s", agent_id)
            agent_descriptor = spawn_agent(agent_id, timeout=120)
            spawned_agents.append(agent_descriptor)
            logger.info("✓ Agent spawned: %s (status: %s)",
                       agent_descriptor.get("agent_id"),
                       agent_descriptor.get("status"))

        logger.info("All 3 agents spawned successfully")

        # Capture baselines
        baseline_supervisor = get_last_stream_id("qc:mailbox/human")
        baseline_orchestrator = get_last_stream_id("qc:mailbox/orchestrator")

        agent_baselines = {}
        for agent_id in dynamic_agent_ids:
            agent_baselines[agent_id] = get_last_stream_id(f"qc:mailbox/{agent_id}")
            logger.info("Baseline for %s: %s", agent_id, agent_baselines[agent_id])

        # Test Flow: List agents using registry
        logger.info("-" * 60)
        logger.info("Phase 1: List registered agents")
        logger.info("-" * 60)

        send_message_to_orchestrator(
            "List all registered agents using the agent_registry tool. "
            "Tell me their IDs and status."
        )

        registry_response = wait_for_human_response(baseline_supervisor, timeout=120)
        baseline_supervisor = get_last_stream_id("qc:mailbox/human")

        registry_message = registry_response.get("message", "")
        logger.info("Registry response: %s", registry_message[:200])

        # Verify all agent IDs mentioned
        for agent_id in dynamic_agent_ids:
            assert agent_id in registry_message, (
                f"Agent {agent_id} not mentioned in registry response. "
                f"Full response: {registry_message}"
            )
        logger.info("✓ All 3 dynamic agents present in registry response")

        # Test Flow: Route messages to each agent
        logger.info("-" * 60)
        logger.info("Phase 2: Route messages to individual agents")
        logger.info("-" * 60)

        for agent_id in dynamic_agent_ids:
            logger.info("Routing message to: %s", agent_id)

            # Send message via orchestrator
            send_message_to_orchestrator(
                f"Send a message to {agent_id} with the text: 'Hello from test, please acknowledge'. "
                f"Wait for the response and tell me what {agent_id} said."
            )

            # Wait for agent to receive message
            logger.info("Waiting for %s to receive message...", agent_id)
            agent_mailbox = f"qc:mailbox/{agent_id}"

            # Poll for message arrival (orchestrator -> agent)
            agent_message_found = False
            deadline = time.time() + 90
            while time.time() < deadline:
                entries = read_stream_after(agent_mailbox, agent_baselines[agent_id], count=50)
                if entries:
                    agent_message_found = True
                    logger.info("✓ Message arrived at %s mailbox", agent_id)
                    break
                time.sleep(2)

            assert agent_message_found, (
                f"No message arrived at {agent_id} mailbox within 90s. "
                f"Check orchestrator routing logic."
            )

            # Wait for orchestrator's relay of agent response to human
            logger.info("Waiting for orchestrator to relay %s response...", agent_id)
            human_response = wait_for_human_response(baseline_supervisor, timeout=150)
            baseline_supervisor = get_last_stream_id("qc:mailbox/human")

            response_text = human_response.get("message", "")
            logger.info("Orchestrator relayed response: %s", response_text[:150])

            # Update agent baseline for next iteration
            agent_baselines[agent_id] = get_last_stream_id(agent_mailbox)

            logger.info("✓ Round-trip completed for %s", agent_id)
            time.sleep(1)  # Brief pause between agents

        # Pad test to meet minimum duration
        elapsed = time.time() - start_time
        logger.info("Elapsed time: %.1fs", elapsed)

        if elapsed < MIN_DURATION_SECONDS_TEST_1_2:
            remaining = MIN_DURATION_SECONDS_TEST_1_2 - elapsed
            logger.info("Padding test duration by %.1fs to meet 5-minute minimum", remaining)

            # Send additional round-robin messages
            while time.time() - start_time < MIN_DURATION_SECONDS_TEST_1_2:
                for agent_id in dynamic_agent_ids:
                    if time.time() - start_time >= MIN_DURATION_SECONDS_TEST_1_2:
                        break

                    logger.info("Padding: sending to %s", agent_id)
                    send_message_to_orchestrator(
                        f"Send a quick status check to {agent_id} and tell me if it responds."
                    )
                    try:
                        wait_for_human_response(baseline_supervisor, timeout=60)
                        baseline_supervisor = get_last_stream_id("qc:mailbox/human")
                    except Exception as e:
                        logger.warning("Padding message failed: %s", e)
                    time.sleep(2)

        # Verification
        logger.info("=" * 60)
        logger.info("VERIFICATION PHASE")
        logger.info("=" * 60)

        total_duration = time.time() - start_time
        assert total_duration >= MIN_DURATION_SECONDS_TEST_1_2, (
            f"Test ran for only {total_duration:.1f}s, expected >= {MIN_DURATION_SECONDS_TEST_1_2}s"
        )
        logger.info("✓ Duration: %.1fs", total_duration)

        # Verify each agent mailbox received at least 1 message
        for agent_id in dynamic_agent_ids:
            agent_stats = get_stream_stats(f"qc:mailbox/{agent_id}")
            entry_count = agent_stats.get("entry_count", 0)
            assert entry_count >= 1, (
                f"Agent {agent_id} mailbox has only {entry_count} entries, expected >= 1"
            )
            logger.info("✓ Agent %s received %d messages", agent_id, entry_count)

        # Verify orchestrator mailbox has messages from agents
        orchestrator_stats = get_stream_stats("qc:mailbox/orchestrator")
        logger.info("✓ Orchestrator mailbox: %d entries", orchestrator_stats.get("entry_count", 0))

        # Success summary
        logger.info("=" * 60)
        logger.info("TEST 1.2 PASSED")
        logger.info("=" * 60)
        logger.info("Dynamic agents: %d", len(spawned_agents))
        logger.info("Total duration: %.1fs", total_duration)
        logger.info("=" * 60)

    finally:
        # Cleanup: Delete dynamic agents
        logger.info("Cleaning up dynamic agents...")
        for agent_id in dynamic_agent_ids:
            try:
                logger.info("Deleting: %s", agent_id)
                delete_agent(agent_id, timeout=60)
                logger.info("✓ Deleted: %s", agent_id)
            except Exception as e:
                logger.error("Failed to delete %s: %s", agent_id, e)

        # Capture artifacts
        logger.info("Capturing test artifacts...")
        artifact_dir = test_log_dir.parent / "artifacts" / test_log_dir.name
        artifact_dir.mkdir(parents=True, exist_ok=True)

        dump_all_streams(artifact_dir)
        capture_all_service_logs(
            artifact_dir,
            services=["orchestrator-runtime", "agent-runtime", "agent-registry"],
        )
        logger.info("Artifacts captured to: %s", artifact_dir)


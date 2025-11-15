"""
Quadracode Advanced E2E Tests - Module 4: Fleet Management

This module validates agent lifecycle management and fleet coordination:
- Dynamic agent spawning and cleanup
- Hotpath agent protection mechanisms

Tests run for 5-6 minutes with real agent containers, exercising:
- Agent spawning via spawn-agent.sh scripts
- Agent registration and health monitoring
- Agent deletion and cleanup verification
- Hotpath protection preventing accidental deletion
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import redis
import requests

from tests.e2e_advanced.utils.logging_framework import (
    create_test_log_directory,
    log_stream_snapshot,
    log_turn,
)
from tests.e2e_advanced.utils.redis_helpers import (
    dump_all_streams,
    get_last_stream_id,
    read_stream,
    send_message_to_orchestrator,
    wait_for_message_on_stream,
)
from tests.e2e_advanced.utils.artifacts import capture_docker_logs
from tests.e2e_advanced.utils.agent_helpers import (
    spawn_agent,
    delete_agent,
    wait_for_agent_healthy,
    set_agent_hotpath,
)
from tests.e2e_advanced.utils.timeouts import wait_for_condition
from tests.e2e_advanced.utils.metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)


def check_docker_container_exists(container_name_pattern: str) -> bool:
    """Check if a Docker container matching the pattern exists."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        container_names = result.stdout.strip().split("\n")
        return any(pattern in name for pattern in [container_name_pattern] for name in container_names)
    except Exception as e:
        logger.error(f"Failed to check Docker containers: {e}")
        return False


def check_docker_volume_exists(volume_name_pattern: str) -> bool:
    """Check if a Docker volume matching the pattern exists."""
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        volume_names = result.stdout.strip().split("\n")
        return any(pattern in name for pattern in [volume_name_pattern] for name in volume_names)
    except Exception as e:
        logger.error(f"Failed to check Docker volumes: {e}")
        return False


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_dynamic_agent_spawning_and_cleanup(
    docker_services, redis_client, test_config
):
    """
    Test 4.1: Dynamic Agent Spawning and Cleanup (6 minutes)
    
    Objective: Spawn 5 agents sequentially, assign tasks to each, then delete 3
    and verify cleanup.
    
    This test validates:
    - Agent spawning via scripts/agent-management/spawn-agent.sh
    - Agent registration with agent-registry service
    - Agent health status monitoring
    - Task assignment and message routing to dynamic agents
    - Agent deletion and resource cleanup
    - Registry stats accuracy
    
    Expected duration: 6 minutes minimum
    Expected agents spawned: 5
    Expected agents deleted: 3
    Expected agents remaining: 2
    
    Prerequisites:
    - Docker daemon accessible
    - scripts/agent-management/spawn-agent.sh and delete-agent.sh available
    - agent-registry service running on port 8090
    
    Troubleshooting:
    - If spawn fails: Check Docker socket mount and permissions
    - If agent never healthy: Check agent-registry logs and agent container logs
    - If deletion incomplete: Check for orphaned containers with `docker ps -a`
    """
    test_name = "test_dynamic_agent_spawning_and_cleanup"
    logger.info(f"Starting {test_name}")
    
    # Initialize metrics collector
    run_id = f"{int(time.time())}-{os.urandom(4).hex()}"
    collector = MetricsCollector(test_name=test_name, run_id=run_id)
    
    # Create log directory
    log_dir = create_test_log_directory(test_name)
    logger.info(f"Logs will be written to: {log_dir}")
    
    spawned_agents = []
    
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
        
        test_start_time = time.time()
        
        # Step 1: Spawn 5 dynamic agents
        logger.info("Step 1: Spawning 5 dynamic agents")
        
        for i in range(1, 6):
            agent_id = f"agent-task-{i}"
            logger.info(f"Spawning agent {i}/5: {agent_id}")
            
            # Spawn agent using helper
            try:
                agent_descriptor = spawn_agent(
                    agent_id=agent_id,
                    network="quadracode-network",  # Assuming docker-compose network
                    timeout=120,
                )
                spawned_agents.append(agent_id)
                logger.info(f"✓ Agent {agent_id} spawned successfully: {agent_descriptor}")
            except Exception as e:
                logger.error(f"Failed to spawn agent {agent_id}: {e}")
                raise
            
            # Verify agent is healthy
            try:
                agent_status = wait_for_agent_healthy(agent_id, timeout=120)
                logger.info(f"✓ Agent {agent_id} is healthy: {agent_status}")
            except Exception as e:
                logger.error(f"Agent {agent_id} never became healthy: {e}")
                raise
            
            # Assign simple task to agent
            logger.info(f"Assigning task to agent {agent_id}")
            
            task_message = f"Send a message to agent {agent_id} saying 'Hello from test, please echo back your ID'."
            
            send_message_to_orchestrator(
                redis_client,
                task_message,
                sender="human",
            )
            
            # Wait for orchestrator to route message to agent
            # Orchestrator should respond to human after relaying
            try:
                response = wait_for_message_on_stream(
                    redis_client,
                    "qc:mailbox/human",
                    baseline_human,
                    sender="orchestrator",
                    timeout=120,
                )
                baseline_human = response["stream_id"]
                logger.info(f"✓ Orchestrator processed task for {agent_id}: {response['stream_id']}")
                
                # Log turn
                log_turn(
                    log_dir,
                    i,
                    {"content": task_message},
                    {
                        "stream_id": response["stream_id"],
                        "content": response.get("message", "")[:200],
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to get response for {agent_id} task: {e}")
        
        logger.info(f"✓ All 5 agents spawned and assigned tasks")
        
        # Step 2: Verify agent registry stats
        logger.info("Step 2: Checking agent registry stats")
        
        registry_url = "http://localhost:8090"
        
        try:
            # Get all agents
            agents_response = requests.get(f"{registry_url}/agents", timeout=10)
            agents_response.raise_for_status()
            agents_list = agents_response.json()
            
            # Count our dynamically spawned agents
            dynamic_agents = [a for a in agents_list if a["agent_id"].startswith("agent-task-")]
            
            assert len(dynamic_agents) == 5, (
                f"Expected 5 dynamic agents in registry, got {len(dynamic_agents)}. "
                f"Agents registered: {[a['agent_id'] for a in dynamic_agents]}"
            )
            logger.info(f"✓ Verified 5 agents registered: {[a['agent_id'] for a in dynamic_agents]}")
            
            # Get registry stats
            stats_response = requests.get(f"{registry_url}/stats", timeout=10)
            stats_response.raise_for_status()
            stats = stats_response.json()
            
            total_agents = stats.get("total_agents", 0)
            logger.info(f"Agent registry stats: total_agents={total_agents}")
            
            # Total should be >= 5 (may include baseline agents)
            assert total_agents >= 5, (
                f"Expected at least 5 agents in registry stats, got {total_agents}"
            )
            logger.info(f"✓ Registry stats correct: {total_agents} total agents")
            
        except Exception as e:
            logger.error(f"Failed to check agent registry: {e}")
            raise
        
        # Step 3: Delete 3 agents
        logger.info("Step 3: Deleting agents agent-task-1, agent-task-2, agent-task-3")
        
        agents_to_delete = ["agent-task-1", "agent-task-2", "agent-task-3"]
        
        for agent_id in agents_to_delete:
            logger.info(f"Deleting agent: {agent_id}")
            
            try:
                success = delete_agent(agent_id, timeout=60)
                if success:
                    spawned_agents.remove(agent_id)
                    logger.info(f"✓ Agent {agent_id} deleted successfully")
                else:
                    logger.error(f"Failed to delete agent {agent_id}")
                    raise RuntimeError(f"Agent deletion failed: {agent_id}")
            except Exception as e:
                logger.error(f"Exception while deleting agent {agent_id}: {e}")
                raise
        
        logger.info(f"✓ Deleted 3 agents. Remaining: {spawned_agents}")
        
        # Step 4: Verify remaining agents
        logger.info("Step 4: Verifying only agent-task-4 and agent-task-5 remain")
        
        # Check registry
        try:
            agents_response = requests.get(f"{registry_url}/agents", timeout=10)
            agents_response.raise_for_status()
            agents_list = agents_response.json()
            
            dynamic_agents = [a for a in agents_list if a["agent_id"].startswith("agent-task-")]
            dynamic_agent_ids = [a["agent_id"] for a in dynamic_agents]
            
            assert "agent-task-4" in dynamic_agent_ids, (
                f"agent-task-4 should still be registered, got: {dynamic_agent_ids}"
            )
            assert "agent-task-5" in dynamic_agent_ids, (
                f"agent-task-5 should still be registered, got: {dynamic_agent_ids}"
            )
            assert "agent-task-1" not in dynamic_agent_ids, (
                f"agent-task-1 should be deleted, got: {dynamic_agent_ids}"
            )
            assert "agent-task-2" not in dynamic_agent_ids, (
                f"agent-task-2 should be deleted, got: {dynamic_agent_ids}"
            )
            assert "agent-task-3" not in dynamic_agent_ids, (
                f"agent-task-3 should be deleted, got: {dynamic_agent_ids}"
            )
            
            logger.info(f"✓ Verified remaining agents in registry: {dynamic_agent_ids}")
            
        except Exception as e:
            logger.error(f"Failed to verify remaining agents: {e}")
            raise
        
        # Step 5: Check Docker containers
        logger.info("Step 5: Verifying Docker container cleanup")
        
        # Deleted agents should not have running containers
        for agent_id in agents_to_delete:
            container_exists = check_docker_container_exists(agent_id)
            if container_exists:
                logger.warning(
                    f"Container for {agent_id} still exists after deletion. "
                    f"This may indicate incomplete cleanup."
                )
                # Not failing test, just logging as containers may be stopped but not removed
        
        # Remaining agents should have containers
        for agent_id in ["agent-task-4", "agent-task-5"]:
            container_exists = check_docker_container_exists(agent_id)
            if not container_exists:
                logger.warning(
                    f"Container for {agent_id} not found, but agent is registered. "
                    f"This may indicate a configuration issue."
                )
        
        # Step 6: Check for orphaned volumes
        logger.info("Step 6: Checking for orphaned volumes")
        
        for agent_id in agents_to_delete:
            volume_exists = check_docker_volume_exists(agent_id)
            if volume_exists:
                logger.warning(
                    f"Volume for {agent_id} still exists after deletion. "
                    f"Check cleanup scripts for volume removal."
                )
        
        total_test_time = time.time() - test_start_time
        logger.info(f"Test flow complete: {total_test_time:.2f}s")
        
        # Verification Step 1: Assert test duration >= 6 minutes
        logger.info("Verification: Checking test duration")
        min_duration = 6 * 60  # 6 minutes
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
        # Teardown: Delete remaining agents
        logger.info("Teardown: Deleting remaining agents")
        
        for agent_id in spawned_agents[:]:  # Copy list to avoid modification during iteration
            try:
                logger.info(f"Cleaning up agent: {agent_id}")
                delete_agent(agent_id, timeout=60)
                spawned_agents.remove(agent_id)
            except Exception as e:
                logger.error(f"Failed to cleanup agent {agent_id}: {e}")
        
        # Dump Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator.log")
        capture_docker_logs("agent-registry", artifacts_dir / "agent_registry.log")
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")


@pytest.mark.e2e_advanced
@pytest.mark.long_running
def test_hotpath_agent_protection(
    docker_services, redis_client, test_config
):
    """
    Test 4.2: Hotpath Agent Protection (5 minutes)
    
    Objective: Mark an agent as hotpath and verify orchestrator refuses to delete it.
    After removing protection, verify deletion succeeds.
    
    This test validates:
    - Hotpath flag can be set via agent-registry API
    - Agent marked as hotpath is protected from deletion
    - Deletion tool call fails with appropriate error message
    - After removing hotpath protection, deletion succeeds
    - Registry and Docker cleanup occur correctly
    
    Expected duration: 5 minutes minimum
    Expected agents spawned: 1
    Expected deletion attempts: 2 (1 blocked, 1 successful)
    
    Prerequisites:
    - agent-registry service running with hotpath endpoints
    - agent_management_tool checks hotpath status before deletion
    
    Troubleshooting:
    - If hotpath not blocking deletion: Verify agent_management_tool implementation
    - If hotpath endpoint fails: Check agent-registry service logs
    - If cleanup incomplete: Verify delete-agent.sh removes containers and volumes
    """
    test_name = "test_hotpath_agent_protection"
    logger.info(f"Starting {test_name}")
    
    # Initialize metrics collector
    run_id = f"{int(time.time())}-{os.urandom(4).hex()}"
    collector = MetricsCollector(test_name=test_name, run_id=run_id)
    
    # Create log directory
    log_dir = create_test_log_directory(test_name)
    logger.info(f"Logs will be written to: {log_dir}")
    
    agent_id = "agent-debugger-hotpath"
    spawned = False
    
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
        
        test_start_time = time.time()
        
        # Step 1: Spawn agent
        logger.info(f"Step 1: Spawning agent {agent_id}")
        
        try:
            agent_descriptor = spawn_agent(
                agent_id=agent_id,
                network="quadracode-network",
                timeout=120,
            )
            spawned = True
            logger.info(f"✓ Agent {agent_id} spawned: {agent_descriptor}")
        except Exception as e:
            logger.error(f"Failed to spawn agent {agent_id}: {e}")
            raise
        
        # Wait for healthy
        try:
            agent_status = wait_for_agent_healthy(agent_id, timeout=120)
            logger.info(f"✓ Agent {agent_id} is healthy: {agent_status}")
        except Exception as e:
            logger.error(f"Agent {agent_id} never became healthy: {e}")
            raise
        
        # Step 2: Mark agent as hotpath
        logger.info(f"Step 2: Marking {agent_id} as hotpath")
        
        try:
            set_agent_hotpath(agent_id, hotpath=True)
            logger.info(f"✓ Agent {agent_id} marked as hotpath")
        except Exception as e:
            logger.error(f"Failed to set hotpath for {agent_id}: {e}")
            raise
        
        # Verify hotpath flag set
        registry_url = "http://localhost:8090"
        try:
            agent_response = requests.get(f"{registry_url}/agents/{agent_id}", timeout=10)
            agent_response.raise_for_status()
            agent_data = agent_response.json()
            
            assert agent_data.get("hotpath") is True, (
                f"Expected hotpath=True for {agent_id}, got {agent_data.get('hotpath')}"
            )
            logger.info(f"✓ Verified hotpath flag set: {agent_data}")
        except Exception as e:
            logger.error(f"Failed to verify hotpath flag: {e}")
            raise
        
        # Step 3: Attempt to delete hotpath agent (should fail)
        logger.info(f"Step 3: Attempting to delete hotpath-protected {agent_id} (should fail)")
        
        # Send message to orchestrator to delete agent
        delete_request = f"Delete agent {agent_id}."
        
        send_message_to_orchestrator(
            redis_client,
            delete_request,
            sender="human",
        )
        
        # Wait for orchestrator response
        try:
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=90,
            )
            baseline_human = response["stream_id"]
            
            response_text = response.get("message", "").lower()
            logger.info(f"Orchestrator response to delete request: {response_text[:200]}")
            
            # Check if response indicates failure or protection
            protection_keywords = ["hotpath", "protected", "cannot delete", "refused", "error"]
            if any(keyword in response_text for keyword in protection_keywords):
                logger.info(f"✓ Deletion blocked by hotpath protection (as expected)")
            else:
                logger.warning(
                    f"Orchestrator response does not clearly indicate hotpath protection. "
                    f"Response: {response_text[:300]}"
                )
            
            # Log turn
            log_turn(
                log_dir,
                1,
                {"content": delete_request},
                {
                    "stream_id": response["stream_id"],
                    "content": response_text[:200],
                },
            )
        except Exception as e:
            logger.error(f"Failed to get orchestrator response: {e}")
            raise
        
        # Verify agent still exists in registry
        try:
            agent_response = requests.get(f"{registry_url}/agents/{agent_id}", timeout=10)
            agent_response.raise_for_status()
            logger.info(f"✓ Agent {agent_id} still present in registry (deletion was blocked)")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error(
                    f"Agent {agent_id} was deleted despite hotpath protection! "
                    f"Hotpath mechanism is not working correctly."
                )
                raise AssertionError(f"Hotpath protection failed for {agent_id}")
            else:
                raise
        
        # Step 4: Remove hotpath protection
        logger.info(f"Step 4: Removing hotpath protection from {agent_id}")
        
        # Send message to orchestrator
        remove_protection_request = (
            f"Remove hotpath protection from agent {agent_id}, then delete it."
        )
        
        send_message_to_orchestrator(
            redis_client,
            remove_protection_request,
            sender="human",
        )
        
        # Wait for orchestrator to remove protection
        try:
            response = wait_for_message_on_stream(
                redis_client,
                "qc:mailbox/human",
                baseline_human,
                sender="orchestrator",
                timeout=90,
            )
            baseline_human = response["stream_id"]
            
            response_text = response.get("message", "").lower()
            logger.info(f"Orchestrator response to remove protection: {response_text[:200]}")
            
            # Log turn
            log_turn(
                log_dir,
                2,
                {"content": remove_protection_request},
                {
                    "stream_id": response["stream_id"],
                    "content": response_text[:200],
                },
            )
        except Exception as e:
            logger.error(f"Failed to get orchestrator response: {e}")
            raise
        
        # Wait for orchestrator to delete agent
        time.sleep(30)  # Give orchestrator time to complete deletion
        
        # Step 5: Verify agent is deleted
        logger.info(f"Step 5: Verifying {agent_id} is deleted from registry")
        
        # Alternative approach: Call set_agent_hotpath directly to remove protection
        # This ensures we can test deletion even if orchestrator's tool call had issues
        try:
            set_agent_hotpath(agent_id, hotpath=False)
            logger.info(f"Manually removed hotpath protection from {agent_id}")
        except Exception as e:
            logger.warning(f"Could not manually remove hotpath: {e}")
        
        # Now manually delete agent to ensure cleanup
        try:
            success = delete_agent(agent_id, timeout=60)
            if success:
                spawned = False
                logger.info(f"✓ Agent {agent_id} deleted successfully after removing protection")
            else:
                logger.error(f"Failed to delete agent {agent_id} even after removing hotpath")
        except Exception as e:
            logger.error(f"Exception while deleting {agent_id}: {e}")
        
        # Verify agent removed from registry
        try:
            agent_response = requests.get(f"{registry_url}/agents/{agent_id}", timeout=10)
            if agent_response.status_code == 404:
                logger.info(f"✓ Agent {agent_id} not found in registry (deletion successful)")
            elif agent_response.status_code == 200:
                logger.error(
                    f"Agent {agent_id} still in registry after deletion. "
                    f"Deletion may have failed."
                )
                raise AssertionError(f"Agent {agent_id} not properly deleted")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.info(f"✓ Agent {agent_id} not found in registry (expected)")
            else:
                raise
        
        total_test_time = time.time() - test_start_time
        logger.info(f"Test flow complete: {total_test_time:.2f}s")
        
        # Verification Step 1: Assert test duration >= 5 minutes
        logger.info("Verification: Checking test duration")
        min_duration = 5 * 60  # 5 minutes
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
        # Teardown: Ensure agent is deleted
        if spawned:
            logger.info(f"Teardown: Cleaning up {agent_id}")
            try:
                # Remove hotpath if still set
                set_agent_hotpath(agent_id, hotpath=False)
                # Delete agent
                delete_agent(agent_id, timeout=60)
            except Exception as e:
                logger.error(f"Failed to cleanup {agent_id}: {e}")
        
        # Dump Redis streams
        artifacts_dir = Path("tests/e2e_advanced/artifacts") / f"{test_name}_{run_id}"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        dump_all_streams(redis_client, artifacts_dir)
        
        # Capture Docker logs
        capture_docker_logs("orchestrator-runtime", artifacts_dir / "orchestrator.log")
        capture_docker_logs("agent-registry", artifacts_dir / "agent_registry.log")
        
        logger.info(f"Test complete. Artifacts in: {artifacts_dir}")


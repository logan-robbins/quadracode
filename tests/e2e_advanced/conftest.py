"""Pytest configuration and fixtures for advanced E2E tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import redis
import time

# Import base utilities from parent test module
import sys
parent_tests = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(parent_tests))
from test_end_to_end import (
    COMPOSE_CMD,
    ROOT,
    SUPERVISOR_RECIPIENT,
    get_last_stream_id,
    redis_cli,
    require_prerequisites,
    run_compose,
    send_message_to_orchestrator,
    wait_for_container,
    wait_for_redis,
    wait_for_registry_agent,
)

# Import advanced utilities (relative imports)
from .utils.logging_framework import create_test_log_directory
from .utils.metrics_collector import MetricsCollector


@pytest.fixture(scope="session")
def check_prerequisites():
    """Verify prerequisites for advanced E2E tests.

    Checks for:
    - Docker CLI
    - Required environment variables (ANTHROPIC_API_KEY)
    """
    require_prerequisites()


@pytest.fixture(scope="function")
def docker_stack(check_prerequisites):
    """Bring up full Docker Compose stack for a test.

    Yields after all services are healthy, then tears down stack.
    
    If E2E_REUSE_STACK=1 is set, this will reuse existing containers
    instead of rebuilding, which is useful for running tests sequentially
    against a pre-started stack.

    Services started:
    - redis
    - redis-mcp
    - agent-registry
    - orchestrator-runtime
    - agent-runtime
    """
    # Check if we're running inside a container
    in_container = os.environ.get("TEST_MODE") or os.path.exists("/.dockerenv")
    
    if in_container:
        # Running inside test container - services are already running on host
        # Just verify connectivity and flush Redis
        try:
            # Wait for Redis to be available
            wait_for_redis(timeout=30)
            # Flush Redis to start clean for this test
            redis_cli("FLUSHALL")
        except Exception as e:
            pytest.fail(f"Failed to connect to Redis from test container: {e}")
        
        yield
        
        # No teardown needed when running in container
        return
    
    # Original logic for running on host
    reuse_stack = os.environ.get("E2E_REUSE_STACK", "").strip().lower() in {"1", "true", "yes"}
    should_teardown = True
    
    if reuse_stack:
        # Check if containers are already healthy
        try:
            wait_for_container("redis", timeout=5)
            wait_for_container("redis-mcp", timeout=5)
            wait_for_container("agent-registry", timeout=5)
            wait_for_container("orchestrator-runtime", timeout=5)
            wait_for_container("agent-runtime", timeout=5)
            wait_for_redis(timeout=5)
            wait_for_registry_agent("agent-runtime", timeout=10)
            
            # Containers are healthy, reuse them
            should_teardown = False
            redis_cli("FLUSHALL")  # Just flush Redis data
        except Exception:
            # Containers not healthy, need to rebuild
            reuse_stack = False
    
    if not reuse_stack:
        # Teardown any previous stack
        run_compose(["down", "-v"], check=False)

        # Start services
        run_compose(
            [
                "up",
                "--build",
                "-d",
                "redis",
                "redis-mcp",
                "agent-registry",
                "orchestrator-runtime",
                "agent-runtime",
            ]
        )

        # Wait for all services to be healthy
        wait_for_container("redis")
        wait_for_container("redis-mcp")
        wait_for_container("agent-registry")
        wait_for_container("orchestrator-runtime")
        wait_for_container("agent-runtime")
        wait_for_redis()

        # Wait for agent to register
        wait_for_registry_agent("agent-runtime", timeout=120)

        # Flush Redis to start clean
        redis_cli("FLUSHALL")

    yield

    # Only teardown if we created the stack
    if should_teardown and not reuse_stack:
        run_compose(["down", "-v"], check=False)


@pytest.fixture(scope="function")
def docker_services(docker_stack):
    """Alias for docker_stack fixture to match test expectations."""
    # This fixture is just a pass-through to docker_stack
    # The tests expect docker_services but our implementation uses docker_stack
    return docker_stack


@pytest.fixture(scope="session")
def redis_client():
    """Provide a direct Redis client for advanced tests."""
    time.sleep(5)  # Add a delay to allow Redis to initialize
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = int(os.environ.get("E2E_REDIS_PORT", "6379"))
    client = redis.Redis(host=host, port=port, decode_responses=True)

    try:
        client.ping()
    except Exception as exc:  # pragma: no cover - connectivity failure should fail fast
        raise RuntimeError(
            f"Unable to connect to Redis at {host}:{port}. "
            "Ensure docker stack is running before executing advanced tests."
        ) from exc

    yield client
    client.close()


@pytest.fixture(scope="function")
def docker_stack_with_humanclone(check_prerequisites):
    """Bring up Docker stack including HumanClone runtime.

    This fixture is for tests that need PRP rejection cycles.
    
    If E2E_REUSE_STACK=1 is set, this will reuse existing containers
    (including human-clone) instead of rebuilding.
    """
    # Check if we're running inside a container
    in_container = os.environ.get("TEST_MODE") or os.path.exists("/.dockerenv")
    
    if in_container:
        # Running inside test container - services are already running on host
        # Just verify connectivity and flush Redis
        try:
            # Wait for Redis to be available
            wait_for_redis(timeout=30)
            # Flush Redis to start clean for this test
            redis_cli("FLUSHALL")
        except Exception as e:
            pytest.fail(f"Failed to connect to Redis from test container: {e}")
        
        yield
        
        # No teardown needed when running in container
        return
    
    # Original logic for running on host
    reuse_stack = os.environ.get("E2E_REUSE_STACK", "").strip().lower() in {"1", "true", "yes"}
    should_teardown = True
    
    if reuse_stack:
        # Check if containers (including human-clone) are already healthy
        try:
            wait_for_container("redis", timeout=5)
            wait_for_container("redis-mcp", timeout=5)
            wait_for_container("agent-registry", timeout=5)
            wait_for_container("orchestrator-runtime", timeout=5)
            wait_for_container("agent-runtime", timeout=5)
            wait_for_container("human-clone-runtime", timeout=5)
            wait_for_redis(timeout=5)
            wait_for_registry_agent("agent-runtime", timeout=10)
            
            # Containers are healthy, reuse them
            should_teardown = False
            redis_cli("FLUSHALL")  # Just flush Redis data
        except Exception:
            # Containers not healthy, need to rebuild
            reuse_stack = False
    
    if not reuse_stack:
        run_compose(["down", "-v"], check=False)

        # Set supervisor to human_clone
        env_override = {"QUADRACODE_SUPERVISOR_RECIPIENT": "human_clone"}

        run_compose(
            [
                "up",
                "--build",
                "-d",
                "redis",
                "redis-mcp",
                "agent-registry",
                "orchestrator-runtime",
                "agent-runtime",
                "human-clone-runtime",
            ],
            env=env_override,
        )

        # Wait for services
        wait_for_container("redis")
        wait_for_container("redis-mcp")
        wait_for_container("agent-registry")
        wait_for_container("orchestrator-runtime")
        wait_for_container("agent-runtime")
        wait_for_container("human-clone-runtime")
        wait_for_redis()

        wait_for_registry_agent("agent-runtime", timeout=120)
        redis_cli("FLUSHALL")

    yield

    # Only teardown if we created the stack
    if should_teardown and not reuse_stack:
        run_compose(["down", "-v"], check=False)


@pytest.fixture(scope="function")
def test_log_dir(request):
    """Create timestamped log directory for the test.

    Returns:
        Path to log directory
    """
    test_name = request.node.name
    log_dir = create_test_log_directory(test_name)
    return log_dir


@pytest.fixture(scope="function")
def metrics_collector(request):
    """Create a MetricsCollector instance for the test.

    Returns:
        MetricsCollector instance
    """
    test_name = request.node.name
    run_id = f"{test_name}_{os.environ.get('BUILD_ID', 'local')}"
    collector = MetricsCollector(test_name=test_name, run_id=run_id)
    return collector


@pytest.fixture(scope="function")
def test_config():
    """Provide test configuration dict.
    
    This fixture exists for backwards compatibility with test function signatures.
    It's currently unused but removing it would require updating all test signatures.
    
    Returns:
        Empty dict for configuration
    """
    return {}


@pytest.fixture(scope="function")
def stream_baselines():
    """Capture baseline stream IDs for comparison.

    Returns:
        Dict with baseline stream IDs for common streams
    """
    return {
        "supervisor": get_last_stream_id(f"qc:mailbox/{SUPERVISOR_RECIPIENT}"),
        "orchestrator": get_last_stream_id("qc:mailbox/orchestrator"),
        "agent_runtime": get_last_stream_id("qc:mailbox/agent-runtime"),
        "context_metrics": get_last_stream_id("qc:context:metrics"),
        "autonomous_events": get_last_stream_id("qc:autonomous:events"),
    }


# Timeout multiplier for CI/slow environments
TIMEOUT_MULTIPLIER = float(os.environ.get("E2E_ADVANCED_TIMEOUT_MULTIPLIER", "1.0"))


def adjusted_timeout(base_timeout: int) -> int:
    """Adjust timeout based on environment multiplier.

    Args:
        base_timeout: Base timeout in seconds

    Returns:
        Adjusted timeout in seconds
    """
    return int(base_timeout * TIMEOUT_MULTIPLIER)


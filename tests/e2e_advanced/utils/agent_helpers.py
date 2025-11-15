"""Agent management helpers for advanced E2E tests.

This module provides utilities for spawning, deleting, and monitoring agents
using the agent-management scripts and agent-registry API.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path to agent management scripts
REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "agent-management"


def spawn_agent(
    agent_id: str,
    network: str = "bridge",
    timeout: int = 120,
    registry_url: str = "http://127.0.0.1:8090",
) -> dict[str, Any]:
    """Spawn a new agent using the spawn-agent.sh script.

    Args:
        agent_id: Unique identifier for the agent
        network: Docker network to connect to (default: "bridge")
        timeout: Maximum seconds to wait for agent to become healthy
        registry_url: Base URL for agent registry

    Returns:
        Agent descriptor dict from registry

    Raises:
        subprocess.CalledProcessError: If spawn script fails
        TimeoutError: If agent doesn't become healthy within timeout

    Example:
        >>> agent = spawn_agent("agent-worker-1", timeout=120)
        >>> assert agent["status"] == "healthy"
        >>> print(f"Spawned agent: {agent['agent_id']}")
    """
    script_path = SCRIPTS_DIR / "spawn-agent.sh"
    if not script_path.exists():
        raise FileNotFoundError(f"spawn-agent.sh not found at {script_path}")

    logger.info("Spawning agent: %s (network: %s)", agent_id, network)

    # Call spawn-agent.sh with agent ID
    env = {
        "QUADRACODE_ID": agent_id,
        "AGENT_RUNTIME_PLATFORM": "docker",
    }

    proc = subprocess.run(
        [str(script_path), agent_id],
        env={**subprocess.os.environ, **env},
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        logger.error("Agent spawn failed: %s", proc.stderr)
        raise subprocess.CalledProcessError(
            proc.returncode,
            proc.args,
            output=proc.stdout,
            stderr=proc.stderr,
        )

    logger.debug("Spawn script output: %s", proc.stdout)

    # Wait for agent to register and become healthy
    agent_record = wait_for_agent_healthy(agent_id, timeout=timeout, registry_url=registry_url)

    logger.info("Agent spawned and healthy: %s", agent_id)
    return agent_record


def delete_agent(
    agent_id: str,
    timeout: int = 60,
    registry_url: str = "http://127.0.0.1:8090",
) -> bool:
    """Delete an agent using the delete-agent.sh script.

    Args:
        agent_id: Unique identifier for the agent
        timeout: Maximum seconds to wait for deletion confirmation
        registry_url: Base URL for agent registry

    Returns:
        True if deletion successful

    Raises:
        subprocess.CalledProcessError: If delete script fails
        RuntimeError: If agent is marked as hotpath (protected)

    Example:
        >>> success = delete_agent("agent-worker-1")
        >>> assert success
    """
    script_path = SCRIPTS_DIR / "delete-agent.sh"
    if not script_path.exists():
        raise FileNotFoundError(f"delete-agent.sh not found at {script_path}")

    # Check if agent is hotpath before attempting deletion
    try:
        agent_info = _fetch_registry_json(f"{registry_url}/agents/{agent_id}")
        if agent_info.get("hotpath") is True:
            raise RuntimeError(
                f"Agent {agent_id} is marked as hotpath (protected) and cannot be deleted. "
                f"Remove hotpath protection first."
            )
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning("Could not check hotpath status for %s: %s", agent_id, e)

    logger.info("Deleting agent: %s", agent_id)

    proc = subprocess.run(
        [str(script_path), agent_id],
        env={**subprocess.os.environ, "AGENT_RUNTIME_PLATFORM": "docker"},
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        logger.error("Agent deletion failed: %s", proc.stderr)
        raise subprocess.CalledProcessError(
            proc.returncode,
            proc.args,
            output=proc.stdout,
            stderr=proc.stderr,
        )

    logger.debug("Delete script output: %s", proc.stdout)

    # Verify agent removed from registry
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _fetch_registry_json(f"{registry_url}/agents/{agent_id}")
            # Agent still exists, wait
            time.sleep(2)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Agent no longer in registry
                logger.info("Agent deleted and removed from registry: %s", agent_id)
                return True
            # Other error, keep trying
            time.sleep(2)
        except Exception:
            # Network error, keep trying
            time.sleep(2)

    logger.warning("Agent may not have been removed from registry: %s", agent_id)
    return True


def wait_for_agent_healthy(
    agent_id: str,
    timeout: int = 120,
    registry_url: str = "http://127.0.0.1:8090",
) -> dict[str, Any]:
    """Wait for an agent to register and become healthy.

    Args:
        agent_id: Unique identifier for the agent
        timeout: Maximum seconds to wait
        registry_url: Base URL for agent registry

    Returns:
        Agent descriptor dict from registry

    Raises:
        TimeoutError: If agent doesn't become healthy within timeout

    Example:
        >>> agent = wait_for_agent_healthy("agent-worker-1", timeout=120)
        >>> assert agent["status"] == "healthy"
    """
    deadline = time.time() + timeout
    iteration = 0

    logger.info("Waiting for agent to become healthy: %s (timeout: %ds)", agent_id, timeout)

    while time.time() < deadline:
        iteration += 1
        try:
            # Fetch all agents from registry
            agents_response = _fetch_registry_json(f"{registry_url}/agents")
            agents = agents_response.get("agents", [])

            # Find our agent
            for agent in agents:
                if agent.get("agent_id") == agent_id:
                    status = agent.get("status")
                    logger.debug(
                        "Agent %s status: %s (iteration %d)",
                        agent_id,
                        status,
                        iteration,
                    )

                    if status == "healthy":
                        logger.info("Agent is healthy: %s", agent_id)
                        return agent

            # Agent not found yet or not healthy, keep waiting
            time.sleep(2)

        except urllib.error.URLError as e:
            logger.debug("Registry not available (iteration %d): %s", iteration, e)
            time.sleep(2)
        except Exception as e:
            logger.debug("Error checking agent status (iteration %d): %s", iteration, e)
            time.sleep(2)

    raise TimeoutError(
        f"Agent {agent_id} did not become healthy within {timeout}s. "
        f"Check that the agent container is running and can reach the registry. "
        f"Try: docker ps | grep {agent_id}"
    )


def set_agent_hotpath(
    agent_id: str,
    hotpath: bool,
    registry_url: str = "http://127.0.0.1:8090",
) -> None:
    """Set or clear hotpath protection for an agent.

    Args:
        agent_id: Unique identifier for the agent
        hotpath: True to protect, False to unprotect
        registry_url: Base URL for agent registry

    Raises:
        urllib.error.HTTPError: If registry request fails

    Example:
        >>> set_agent_hotpath("agent-debugger", hotpath=True)
        >>> # Agent is now protected from deletion
        >>> set_agent_hotpath("agent-debugger", hotpath=False)
        >>> # Protection removed
    """
    url = f"{registry_url}/agents/{agent_id}/hotpath"
    data = json.dumps({"hotpath": hotpath}).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    logger.info("Setting hotpath=%s for agent: %s", hotpath, agent_id)

    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            result = json.load(response)
            logger.debug("Hotpath update result: %s", result)
    except urllib.error.HTTPError as e:
        logger.error("Failed to set hotpath for %s: %s", agent_id, e)
        raise


def get_agent_status(
    agent_id: str,
    registry_url: str = "http://127.0.0.1:8090",
) -> dict[str, Any] | None:
    """Get current status of an agent from the registry.

    Args:
        agent_id: Unique identifier for the agent
        registry_url: Base URL for agent registry

    Returns:
        Agent descriptor dict, or None if not found

    Example:
        >>> status = get_agent_status("agent-worker-1")
        >>> if status:
        ...     print(f"Agent status: {status['status']}")
    """
    try:
        return _fetch_registry_json(f"{registry_url}/agents/{agent_id}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def list_agents(registry_url: str = "http://127.0.0.1:8090") -> list[dict[str, Any]]:
    """List all registered agents.

    Args:
        registry_url: Base URL for agent registry

    Returns:
        List of agent descriptor dicts

    Example:
        >>> agents = list_agents()
        >>> for agent in agents:
        ...     print(f"{agent['agent_id']}: {agent['status']}")
    """
    try:
        response = _fetch_registry_json(f"{registry_url}/agents")
        return response.get("agents", [])
    except Exception as e:
        logger.error("Failed to list agents: %s", e)
        return []


def _fetch_registry_json(url: str) -> dict[str, Any]:
    """Fetch and parse JSON from registry API.

    Args:
        url: Full URL to fetch

    Returns:
        Parsed JSON response

    Raises:
        urllib.error.HTTPError: If request fails
    """
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return json.load(response)


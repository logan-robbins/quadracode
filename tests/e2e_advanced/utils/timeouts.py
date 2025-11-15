"""Timeout and polling utilities for advanced E2E tests.

This module provides generic polling and waiting utilities with detailed
timeout error messages for AI coding agents.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def wait_for_condition(
    condition_fn: Callable[[], bool],
    timeout: int,
    poll_interval: int = 2,
    description: str = "",
) -> bool:
    """Generic polling utility that waits for a condition to become true.

    Args:
        condition_fn: Function that returns True when condition is met
        timeout: Maximum seconds to wait
        poll_interval: Seconds between condition checks
        description: Human-readable description of what we're waiting for

    Returns:
        True if condition met, False if timeout

    Example:
        >>> def orchestrator_ready():
        ...     response = requests.get("http://localhost:8080/health")
        ...     return response.status_code == 200
        >>>
        >>> success = wait_for_condition(
        ...     condition_fn=orchestrator_ready,
        ...     timeout=60,
        ...     description="orchestrator health endpoint"
        ... )
        >>> assert success, "Orchestrator did not become healthy"
    """
    deadline = time.time() + timeout
    iteration = 0

    if description:
        logger.info("Waiting for: %s (timeout: %ds)", description, timeout)

    while time.time() < deadline:
        iteration += 1
        try:
            if condition_fn():
                elapsed = timeout - (deadline - time.time())
                logger.debug(
                    "Condition met after %.1fs (%d iterations): %s",
                    elapsed,
                    iteration,
                    description or "condition",
                )
                return True
        except Exception as e:
            logger.debug(
                "Condition check failed (iteration %d): %s: %s",
                iteration,
                type(e).__name__,
                e,
            )

        time.sleep(poll_interval)

    logger.warning(
        "Timeout waiting for: %s (waited %ds, %d iterations)",
        description or "condition",
        timeout,
        iteration,
    )
    return False


def wait_for_condition_with_result(
    condition_fn: Callable[[], T | None],
    timeout: int,
    poll_interval: int = 2,
    description: str = "",
) -> T | None:
    """Wait for a condition function to return a non-None result.

    Similar to wait_for_condition but returns the result from condition_fn
    instead of just a boolean.

    Args:
        condition_fn: Function that returns result when ready, None otherwise
        timeout: Maximum seconds to wait
        poll_interval: Seconds between condition checks
        description: Human-readable description

    Returns:
        Result from condition_fn if met, None if timeout

    Example:
        >>> def get_agent_status():
        ...     agents = fetch_registry_agents()
        ...     return next((a for a in agents if a["id"] == "test-agent"), None)
        >>>
        >>> agent = wait_for_condition_with_result(
        ...     condition_fn=get_agent_status,
        ...     timeout=120,
        ...     description="agent-test-agent registration",
        ... )
        >>> assert agent is not None, "Agent never registered"
    """
    deadline = time.time() + timeout
    iteration = 0

    if description:
        logger.info("Waiting for: %s (timeout: %ds)", description, timeout)

    while time.time() < deadline:
        iteration += 1
        try:
            result = condition_fn()
            if result is not None:
                elapsed = timeout - (deadline - time.time())
                logger.debug(
                    "Result obtained after %.1fs (%d iterations): %s",
                    elapsed,
                    iteration,
                    description or "result",
                )
                return result
        except Exception as e:
            logger.debug(
                "Condition check raised exception (iteration %d): %s: %s",
                iteration,
                type(e).__name__,
                e,
            )

        time.sleep(poll_interval)

    logger.warning(
        "Timeout waiting for result: %s (waited %ds, %d iterations)",
        description or "result",
        timeout,
        iteration,
    )
    return None


class TimeoutManager:
    """Context manager for tracking operation timeouts with detailed diagnostics.

    Example:
        >>> with TimeoutManager(timeout=60, operation="agent spawn") as tm:
        ...     spawn_agent("test-agent-1")
        ...     tm.checkpoint("agent container created")
        ...     wait_for_agent_healthy("test-agent-1")
        ...     tm.checkpoint("agent registered with registry")
    """

    def __init__(self, timeout: int, operation: str):
        """Initialize timeout manager.

        Args:
            timeout: Maximum seconds allowed for operation
            operation: Description of the operation
        """
        self.timeout = timeout
        self.operation = operation
        self.start_time: float | None = None
        self.checkpoints: list[tuple[float, str]] = []
        self.timed_out = False

    def __enter__(self) -> TimeoutManager:
        """Start the timeout timer."""
        self.start_time = time.time()
        logger.info("Starting operation: %s (timeout: %ds)", self.operation, self.timeout)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Check if timeout was exceeded and log results."""
        if self.start_time is None:
            return

        elapsed = time.time() - self.start_time

        if elapsed > self.timeout:
            self.timed_out = True
            logger.error(
                "Operation TIMEOUT: %s took %.1fs (limit: %ds)",
                self.operation,
                elapsed,
                self.timeout,
            )
            if self.checkpoints:
                logger.error("Checkpoints reached before timeout:")
                for checkpoint_time, checkpoint_desc in self.checkpoints:
                    checkpoint_elapsed = checkpoint_time - self.start_time
                    logger.error("  %.1fs: %s", checkpoint_elapsed, checkpoint_desc)
        else:
            logger.info(
                "Operation completed: %s in %.1fs (limit: %ds)",
                self.operation,
                elapsed,
                self.timeout,
            )
            if self.checkpoints:
                logger.debug("Checkpoints:")
                for checkpoint_time, checkpoint_desc in self.checkpoints:
                    checkpoint_elapsed = checkpoint_time - self.start_time
                    logger.debug("  %.1fs: %s", checkpoint_elapsed, checkpoint_desc)

    def checkpoint(self, description: str) -> None:
        """Record a checkpoint during the operation.

        Args:
            description: Description of what was just completed
        """
        if self.start_time is None:
            return
        self.checkpoints.append((time.time(), description))
        elapsed = time.time() - self.start_time
        logger.debug("Checkpoint (%.1fs): %s", elapsed, description)

    def elapsed(self) -> float:
        """Get elapsed time since operation start.

        Returns:
            Seconds elapsed
        """
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    def remaining(self) -> float:
        """Get remaining time before timeout.

        Returns:
            Seconds remaining (may be negative if timed out)
        """
        if self.start_time is None:
            return self.timeout
        return self.timeout - (time.time() - self.start_time)


def rate_limit_sleep(
    base_delay: float = 0.5,
    reason: str = "rate limiting protection",
) -> None:
    """Sleep to prevent API rate limiting.

    This function provides configurable rate limiting delays that can be
    adjusted via environment variables. By default, it uses a minimal delay
    but can be increased if rate limit issues are encountered.

    Args:
        base_delay: Default delay in seconds (can be overridden by env var)
        reason: Human-readable description of why we're sleeping

    Environment Variables:
        E2E_TURN_COOLDOWN_SECONDS: Override the delay duration (default: base_delay)
        E2E_RATE_LIMIT_DISABLE: Set to "1" to disable all rate limiting delays

    Example:
        >>> # In test code
        >>> for turn in range(30):
        ...     send_message_to_orchestrator("Question?")
        ...     response = wait_for_response()
        ...     rate_limit_sleep(0.5, "between conversation turns")

        >>> # To increase delay when running tests:
        >>> # E2E_TURN_COOLDOWN_SECONDS=2.0 uv run pytest tests/e2e_advanced -v

        >>> # To disable all rate limiting delays:
        >>> # E2E_RATE_LIMIT_DISABLE=1 uv run pytest tests/e2e_advanced -v
    """
    # Check if rate limiting is disabled
    if os.environ.get("E2E_RATE_LIMIT_DISABLE", "").strip().lower() in {"1", "true", "yes"}:
        logger.debug("Rate limit sleep skipped (disabled via E2E_RATE_LIMIT_DISABLE)")
        return

    # Get configured delay
    delay_str = os.environ.get("E2E_TURN_COOLDOWN_SECONDS", str(base_delay))
    try:
        delay = float(delay_str)
    except ValueError:
        logger.warning(
            "Invalid E2E_TURN_COOLDOWN_SECONDS value '%s', using base_delay=%.2f",
            delay_str,
            base_delay,
        )
        delay = base_delay

    if delay > 0:
        logger.debug("Rate limit sleep: %.2fs (%s)", delay, reason)
        time.sleep(delay)


class RateLimiter:
    """Token bucket rate limiter for API calls.

    This class implements a simple token bucket algorithm to limit the rate
    of API calls. It's useful for tests that make many rapid API calls and
    want to stay well below rate limits.

    Example:
        >>> # Create a rate limiter: 50 requests per minute
        >>> limiter = RateLimiter(max_requests_per_minute=50)
        >>>
        >>> # In test loop
        >>> for i in range(100):
        ...     limiter.wait_if_needed()  # Will sleep if we're going too fast
        ...     send_message_to_orchestrator(f"Message {i}")
        ...     response = wait_for_response()
    """

    def __init__(self, max_requests_per_minute: float = 50.0):
        """Initialize rate limiter.

        Args:
            max_requests_per_minute: Maximum requests allowed per minute
                                    (default: 50, well below Anthropic's 4000 RPM limit)
        """
        self.max_rpm = max_requests_per_minute
        self.min_interval = 60.0 / max_requests_per_minute  # seconds between requests
        self.last_request_time: float | None = None
        logger.info(
            "RateLimiter initialized: %.1f requests/minute (%.2fs minimum interval)",
            self.max_rpm,
            self.min_interval,
        )

    def wait_if_needed(self) -> None:
        """Wait if necessary to stay within rate limits.

        This method should be called before each API request. It will sleep
        if insufficient time has passed since the last request.
        """
        now = time.time()

        if self.last_request_time is not None:
            elapsed = now - self.last_request_time
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                logger.debug(
                    "Rate limiter: sleeping %.2fs (%.1f RPM target)",
                    sleep_time,
                    self.max_rpm,
                )
                time.sleep(sleep_time)
                now = time.time()

        self.last_request_time = now

    def reset(self) -> None:
        """Reset the rate limiter state.

        Useful when starting a new test or after a long pause.
        """
        self.last_request_time = None
        logger.debug("RateLimiter reset")


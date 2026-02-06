"""
Redis client management for Quadracode UI.

Provides cached Redis connections and basic stream operations.
Supports QUADRACODE_MOCK_MODE for standalone testing with fakeredis.
"""

import json
import logging
from datetime import UTC, datetime

import redis
import streamlit as st

from quadracode_ui.config import MOCK_MODE, REDIS_HOST, REDIS_PORT

LOGGER = logging.getLogger(__name__)


def _seed_mock_data(client: "redis.Redis") -> None:
    """Seeds mock data into fakeredis for demonstration purposes."""
    now = datetime.now(UTC)
    
    # Seed sample mailbox messages
    sample_messages = [
        {
            "mailbox": "qc:mailbox/human",
            "timestamp": now.isoformat(),
            "sender": "orchestrator",
            "recipient": "human",
            "message": "[MOCK] Welcome to Quadracode UI in mock mode!",
            "payload": json.dumps({
                "chat_id": "mock-chat-001",
                "thread_id": "mock-thread-001",
                "ticket_id": "mock-ticket-001",
            }),
        },
        {
            "mailbox": "qc:mailbox/orchestrator",
            "timestamp": now.isoformat(),
            "sender": "human",
            "recipient": "orchestrator",
            "message": "[MOCK] Sample task: Demonstrate mock mode functionality",
            "payload": json.dumps({
                "chat_id": "mock-chat-001",
                "thread_id": "mock-thread-001",
                "ticket_id": "mock-ticket-002",
            }),
        },
    ]
    
    for msg in sample_messages:
        mailbox = msg.pop("mailbox")
        try:
            client.xadd(mailbox, msg, maxlen=100)
        except Exception:
            pass  # Ignore errors in seeding
    
    # Seed sample context metrics
    context_metrics = {
        "event": "post_process",
        "timestamp": now.isoformat(),
        "payload": json.dumps({
            "quality_score": 0.85,
            "focus_metric": "task_completion",
            "context_window_used": 4500,
            "operation": "summarize",
        }),
    }
    try:
        client.xadd("qc:context:metrics", context_metrics, maxlen=200)
    except Exception:
        pass
    
    # Seed sample autonomous events
    autonomous_event = {
        "event": "checkpoint",
        "timestamp": now.isoformat(),
        "payload": json.dumps({
            "thread_id": "mock-thread-001",
            "iteration": 1,
            "status": "running",
        }),
    }
    try:
        client.xadd("qc:autonomous:events", autonomous_event, maxlen=200)
    except Exception:
        pass
    
    LOGGER.info("[MOCK] Seeded sample data for demonstration")


@st.cache_resource(show_spinner=False)
def get_redis_client() -> "redis.Redis":
    """
    Initializes and returns a Redis client instance.

    The client connection is cached as a Streamlit resource to ensure that a
    single connection is reused across reruns.
    
    In mock mode (QUADRACODE_MOCK_MODE=true), uses fakeredis for in-memory
    operation without external Redis dependency.
    """
    if MOCK_MODE:
        try:
            import fakeredis
            
            LOGGER.info("[MOCK] Using fakeredis for mock mode operation")
            client = fakeredis.FakeRedis(decode_responses=True)
            
            # Seed mock data for demonstration
            _seed_mock_data(client)
            
            return client
        except ImportError:
            LOGGER.warning("[MOCK] fakeredis not installed, falling back to real Redis")
    
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def test_redis_connection(client: "redis.Redis") -> tuple[bool, str | None]:
    """
    Tests the Redis connection.

    Args:
        client: The Redis client to test.

    Returns:
        A tuple of (success, error_message).
    """
    try:
        client.ping()
        if MOCK_MODE:
            return True, "[MOCK MODE]"
        return True, None
    except redis.RedisError as exc:
        return False, str(exc)


def list_mailboxes(client: "redis.Redis", pattern: str = "qc:mailbox/*") -> list[str]:
    """
    Scans for and returns a sorted list of mailbox stream keys.

    Args:
        client: The Redis client to use for the SCAN operation.
        pattern: The key pattern to match.

    Returns:
        A sorted list of stream keys matching the pattern.
    """
    try:
        keys = sorted({key for key in client.scan_iter(pattern)})
    except redis.RedisError:
        # In mock mode, return default mailboxes if scan fails
        if MOCK_MODE:
            return ["qc:mailbox/human", "qc:mailbox/orchestrator"]
        return []
    
    # Ensure we have at least the default mailboxes in mock mode
    if MOCK_MODE and not keys:
        return ["qc:mailbox/human", "qc:mailbox/orchestrator"]
    
    return keys


def is_mock_mode() -> bool:
    """Returns True if running in mock mode."""
    return MOCK_MODE



"""
Redis client management for Quadracode UI.

Provides cached Redis connections and basic stream operations.
"""

import redis
import streamlit as st

from quadracode_ui.config import REDIS_HOST, REDIS_PORT


@st.cache_resource(show_spinner=False)
def get_redis_client() -> redis.Redis:
    """
    Initializes and returns a Redis client instance.

    The client connection is cached as a Streamlit resource to ensure that a
    single connection is reused across reruns.
    """
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def test_redis_connection(client: redis.Redis) -> tuple[bool, str | None]:
    """
    Tests the Redis connection.

    Args:
        client: The Redis client to test.

    Returns:
        A tuple of (success, error_message).
    """
    try:
        client.ping()
        return True, None
    except redis.RedisError as exc:
        return False, str(exc)


def list_mailboxes(client: redis.Redis, pattern: str = "qc:mailbox/*") -> list[str]:
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
        return []
    return keys



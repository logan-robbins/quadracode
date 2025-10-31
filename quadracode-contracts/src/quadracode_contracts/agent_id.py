"""
Agent ID utilities for UUID-based agent identification.
"""
import hashlib
import uuid
from typing import Optional


def generate_agent_id() -> str:
    """Generate a random agent ID in format: agent-{SHORT_UUID}."""
    short_uuid = str(uuid.uuid4())[:8]
    return f"agent-{short_uuid}"

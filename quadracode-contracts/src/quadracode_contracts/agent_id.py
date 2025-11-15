"""
This module provides standardized utilities for generating unique, UUID-based 
identifiers for Quadracode agents.

Consistent and recognizable agent IDs are crucial for tracking, messaging, and 
resource management across the distributed system. This module centralizes the 
ID generation logic to ensure that all agents conform to a standard format, 
which includes a descriptive prefix and a short, unique identifier.
"""
import hashlib
import uuid
from typing import Optional


def generate_agent_id() -> str:
    """
    Generates a random, unique identifier for an agent.

    This function creates a new agent ID using a shortened version of a UUID, 
    prefixed with "agent-". The use of a short UUID provides a balance between 
    uniqueness and readability, making the IDs easier to work with in logs and 
    user interfaces.

    Returns:
        A new agent ID string in the format: "agent-{SHORT_UUID}".
    """
    short_uuid = str(uuid.uuid4())[:8]
    return f"agent-{short_uuid}"

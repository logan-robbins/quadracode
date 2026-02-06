"""
Standardized utilities for generating unique, UUID-based identifiers for
Quadracode agents.

Consistent and recognizable agent IDs are crucial for tracking, messaging, and
resource management across the distributed system. This module centralizes the
ID generation logic to ensure that all agents conform to a standard format,
which includes a descriptive prefix and a short, unique identifier.
"""
from __future__ import annotations

import uuid


def generate_agent_id() -> str:
    """Generate a random, unique identifier for an agent.

    Creates a new agent ID using a shortened version of a UUID4, prefixed with
    ``agent-``.  The 8-character hex segment gives ~4 billion unique values
    which is more than sufficient for fleet-scale uniqueness while remaining
    readable in logs and UIs.

    Returns:
        A new agent ID string in the format ``agent-{SHORT_UUID}``.
    """
    short_uuid = str(uuid.uuid4())[:8]
    return f"agent-{short_uuid}"

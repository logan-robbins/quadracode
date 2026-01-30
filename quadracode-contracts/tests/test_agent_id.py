"""Tests for agent_id module."""
import re
from quadracode_contracts.agent_id import generate_agent_id


class TestGenerateAgentId:
    """Tests for generate_agent_id function."""

    def test_generates_valid_format(self):
        """Agent ID should match the expected 'agent-XXXXXXXX' format."""
        agent_id = generate_agent_id()
        assert agent_id.startswith("agent-")
        # UUID portion should be 8 hex characters
        uuid_part = agent_id.replace("agent-", "")
        assert len(uuid_part) == 8
        assert re.match(r"^[a-f0-9]{8}$", uuid_part)

    def test_generates_unique_ids(self):
        """Multiple calls should generate unique agent IDs."""
        ids = {generate_agent_id() for _ in range(100)}
        assert len(ids) == 100  # All should be unique

    def test_id_is_lowercase(self):
        """Agent ID should be lowercase."""
        for _ in range(10):
            agent_id = generate_agent_id()
            assert agent_id == agent_id.lower()

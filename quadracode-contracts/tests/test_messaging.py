"""Tests for messaging module."""
import json
from datetime import datetime, timezone

from quadracode_contracts.messaging import (
    MessageEnvelope,
    MAILBOX_PREFIX,
    ORCHESTRATOR_RECIPIENT,
    HUMAN_RECIPIENT,
    HUMAN_CLONE_RECIPIENT,
    mailbox_key,
    mailbox_recipient,
    agent_mailbox,
)


class TestConstants:
    """Tests for messaging constants."""

    def test_mailbox_prefix(self):
        """Should have expected prefix."""
        assert MAILBOX_PREFIX == "qc:mailbox/"

    def test_recipient_constants(self):
        """Should have expected recipient constants."""
        assert ORCHESTRATOR_RECIPIENT == "orchestrator"
        assert HUMAN_RECIPIENT == "human"
        assert HUMAN_CLONE_RECIPIENT == "human_clone"


class TestMailboxFunctions:
    """Tests for mailbox helper functions."""

    def test_mailbox_key_creates_full_key(self):
        """Should create full Redis key from recipient."""
        assert mailbox_key("orchestrator") == "qc:mailbox/orchestrator"
        assert mailbox_key("agent-abc123") == "qc:mailbox/agent-abc123"

    def test_mailbox_recipient_extracts_id(self):
        """Should extract recipient ID from full key."""
        assert mailbox_recipient("qc:mailbox/orchestrator") == "orchestrator"
        assert mailbox_recipient("qc:mailbox/agent-xyz789") == "agent-xyz789"

    def test_mailbox_recipient_handles_raw_id(self):
        """Should return raw ID if no prefix present."""
        assert mailbox_recipient("agent-123") == "agent-123"

    def test_agent_mailbox_creates_agent_key(self):
        """Should create mailbox key for agent."""
        assert agent_mailbox("agent-worker-01") == "qc:mailbox/agent-worker-01"


class TestMessageEnvelope:
    """Tests for MessageEnvelope model."""

    def test_minimal_envelope(self):
        """Should create envelope with required fields."""
        envelope = MessageEnvelope(
            sender="human",
            recipient="orchestrator",
            message="task_request"
        )
        assert envelope.sender == "human"
        assert envelope.recipient == "orchestrator"
        assert envelope.message == "task_request"
        assert envelope.payload == {}
        assert envelope.timestamp is not None

    def test_envelope_with_payload(self):
        """Should create envelope with payload."""
        envelope = MessageEnvelope(
            sender="orchestrator",
            recipient="agent-abc123",
            message="delegate_task",
            payload={
                "chat_id": "chat-2024-001",
                "thread_id": "thread-xyz",
                "task": "Analyze code coverage reports"
            }
        )
        assert envelope.payload["chat_id"] == "chat-2024-001"
        assert envelope.payload["task"] == "Analyze code coverage reports"

    def test_timestamp_auto_populated(self):
        """Timestamp should be auto-populated with ISO format."""
        envelope = MessageEnvelope(
            sender="test",
            recipient="test",
            message="test"
        )
        # Should be parseable as ISO timestamp
        parsed = datetime.fromisoformat(envelope.timestamp.replace("Z", "+00:00"))
        assert parsed is not None

    def test_to_stream_fields_serialization(self):
        """Should serialize to Redis-compatible format."""
        envelope = MessageEnvelope(
            sender="orchestrator",
            recipient="human",
            message="status_update",
            payload={"progress": 75, "stage": "validation"}
        )
        fields = envelope.to_stream_fields()

        assert isinstance(fields, dict)
        assert all(isinstance(v, str) for v in fields.values())
        assert fields["sender"] == "orchestrator"
        assert fields["recipient"] == "human"
        assert fields["message"] == "status_update"

        # Payload should be JSON string
        payload_parsed = json.loads(fields["payload"])
        assert payload_parsed["progress"] == 75

    def test_from_stream_fields_deserialization(self):
        """Should deserialize from Redis format."""
        fields = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "sender": "agent-123",
            "recipient": "orchestrator",
            "message": "task_complete",
            "payload": '{"result":"success","metrics":{"latency_ms":150}}'
        }
        envelope = MessageEnvelope.from_stream_fields(fields)

        assert envelope.sender == "agent-123"
        assert envelope.recipient == "orchestrator"
        assert envelope.message == "task_complete"
        assert envelope.payload["result"] == "success"
        assert envelope.payload["metrics"]["latency_ms"] == 150

    def test_from_stream_fields_handles_missing_payload(self):
        """Should handle missing payload gracefully."""
        fields = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "sender": "test",
            "recipient": "test",
            "message": "ping"
        }
        envelope = MessageEnvelope.from_stream_fields(fields)
        assert envelope.payload == {}

    def test_from_stream_fields_handles_invalid_json(self):
        """Should handle malformed JSON payload."""
        fields = {
            "timestamp": "2024-01-15T10:30:00+00:00",
            "sender": "test",
            "recipient": "test",
            "message": "test",
            "payload": "not valid json {"
        }
        envelope = MessageEnvelope.from_stream_fields(fields)
        # Should store raw value under _raw key
        assert "_raw" in envelope.payload
        assert envelope.payload["_raw"] == "not valid json {"

    def test_from_stream_fields_defaults_for_missing(self):
        """Should provide defaults for missing fields."""
        fields = {}
        envelope = MessageEnvelope.from_stream_fields(fields)
        assert envelope.sender == "unknown"
        assert envelope.recipient == "unknown"
        assert envelope.message == ""

    def test_roundtrip_serialization(self):
        """Should survive roundtrip through stream fields."""
        original = MessageEnvelope(
            sender="orchestrator",
            recipient="agent-test",
            message="complex_task",
            payload={
                "chat_id": "chat-roundtrip-test",
                "nested": {
                    "items": [1, 2, 3],
                    "flag": True
                }
            }
        )
        fields = original.to_stream_fields()
        restored = MessageEnvelope.from_stream_fields(fields)

        assert restored.sender == original.sender
        assert restored.recipient == original.recipient
        assert restored.message == original.message
        assert restored.payload["chat_id"] == original.payload["chat_id"]
        assert restored.payload["nested"]["items"] == [1, 2, 3]

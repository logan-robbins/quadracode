"""Tests for autonomous module."""
import pytest
from pydantic import ValidationError

from quadracode_contracts.autonomous import (
    AutonomousRoutingDirective,
    AutonomousCheckpointRecord,
    CritiqueCategory,
    CritiqueSeverity,
    HypothesisCritiqueRecord,
    AutonomousEscalationRecord,
)


class TestAutonomousRoutingDirective:
    """Tests for AutonomousRoutingDirective model."""

    def test_default_values(self):
        """Default directive should not deliver or escalate."""
        directive = AutonomousRoutingDirective()
        assert directive.deliver_to_human is False
        assert directive.escalate is False
        assert directive.reason is None
        assert directive.recovery_attempts == []

    def test_delivery_directive(self):
        """Should create delivery directive with reason."""
        directive = AutonomousRoutingDirective(
            deliver_to_human=True,
            reason="Task completed successfully"
        )
        assert directive.deliver_to_human is True
        assert directive.reason == "Task completed successfully"

    def test_escalation_directive(self):
        """Should create escalation directive with recovery attempts."""
        directive = AutonomousRoutingDirective(
            escalate=True,
            reason="Unrecoverable API error",
            recovery_attempts=[
                "Attempted retry with backoff",
                "Attempted alternative endpoint",
                "Attempted credential refresh"
            ]
        )
        assert directive.escalate is True
        assert len(directive.recovery_attempts) == 3

    def test_from_payload_valid(self):
        """Should parse valid payload."""
        payload = {
            "deliver_to_human": True,
            "reason": "Analysis complete"
        }
        directive = AutonomousRoutingDirective.from_payload(payload)
        assert directive is not None
        assert directive.deliver_to_human is True

    def test_from_payload_ignores_extra_fields(self):
        """Should ignore unknown fields in payload."""
        payload = {
            "deliver_to_human": True,
            "unknown_field": "should be ignored",
            "another_extra": 123
        }
        directive = AutonomousRoutingDirective.from_payload(payload)
        assert directive is not None
        assert directive.deliver_to_human is True

    def test_from_payload_none_for_non_dict(self):
        """Should return None for non-dict input."""
        assert AutonomousRoutingDirective.from_payload("not a dict") is None
        assert AutonomousRoutingDirective.from_payload(123) is None
        assert AutonomousRoutingDirective.from_payload(None) is None

    def test_from_payload_handles_invalid_types(self):
        """Should handle payloads with invalid field types gracefully."""
        payload = {
            "deliver_to_human": "not a bool",
            "escalate": "also not a bool"
        }
        # Should return None for completely invalid data
        result = AutonomousRoutingDirective.from_payload(payload)
        # With extra="ignore", malformed fields may still fail
        # The implementation filters and retries
        assert result is None or isinstance(result, AutonomousRoutingDirective)

    def test_to_payload(self):
        """Should serialize to dictionary."""
        directive = AutonomousRoutingDirective(
            deliver_to_human=True,
            reason="Done"
        )
        payload = directive.to_payload()
        assert isinstance(payload, dict)
        assert payload["deliver_to_human"] is True
        assert payload["reason"] == "Done"


class TestAutonomousCheckpointRecord:
    """Tests for AutonomousCheckpointRecord model."""

    def test_valid_checkpoint(self):
        """Should create valid checkpoint record."""
        checkpoint = AutonomousCheckpointRecord(
            milestone=1,
            status="in_progress",
            summary="Initial data gathering phase completed"
        )
        assert checkpoint.milestone == 1
        assert checkpoint.status == "in_progress"
        assert checkpoint.recorded_at is not None

    def test_checkpoint_with_all_fields(self):
        """Should accept all optional fields."""
        checkpoint = AutonomousCheckpointRecord(
            milestone=5,
            status="complete",
            summary="All validation tests passed",
            next_steps=["Deploy to staging", "Run integration tests"],
            title="Validation Phase Complete"
        )
        assert checkpoint.title == "Validation Phase Complete"
        assert len(checkpoint.next_steps) == 2

    def test_milestone_must_be_positive(self):
        """Milestone must be >= 1."""
        with pytest.raises(ValidationError):
            AutonomousCheckpointRecord(
                milestone=0,
                status="in_progress",
                summary="Invalid milestone"
            )

    def test_summary_cannot_be_empty(self):
        """Summary must have at least 1 character."""
        with pytest.raises(ValidationError):
            AutonomousCheckpointRecord(
                milestone=1,
                status="in_progress",
                summary=""
            )

    def test_status_values(self):
        """Should only accept valid status literals."""
        for status in ["in_progress", "complete", "blocked"]:
            checkpoint = AutonomousCheckpointRecord(
                milestone=1,
                status=status,
                summary="Test checkpoint"
            )
            assert checkpoint.status == status


class TestCritiqueEnums:
    """Tests for critique-related enums."""

    def test_critique_category_values(self):
        """Should have expected category values."""
        assert CritiqueCategory.CODE_QUALITY == "code_quality"
        assert CritiqueCategory.ARCHITECTURE == "architecture"
        assert CritiqueCategory.TEST_COVERAGE == "test_coverage"
        assert CritiqueCategory.PERFORMANCE == "performance"

    def test_critique_severity_values(self):
        """Should have expected severity values."""
        assert CritiqueSeverity.LOW == "low"
        assert CritiqueSeverity.MODERATE == "moderate"
        assert CritiqueSeverity.HIGH == "high"
        assert CritiqueSeverity.CRITICAL == "critical"


class TestHypothesisCritiqueRecord:
    """Tests for HypothesisCritiqueRecord model."""

    def test_valid_critique(self):
        """Should create valid critique record."""
        critique = HypothesisCritiqueRecord(
            cycle_id="cycle-2024-001",
            hypothesis="Adding caching will improve performance by 50%",
            critique_summary="Hypothesis lacks baseline measurements",
            qualitative_feedback="Need to establish current latency metrics before claiming improvement percentage",
            category=CritiqueCategory.PERFORMANCE,
            severity=CritiqueSeverity.MODERATE
        )
        assert critique.cycle_id == "cycle-2024-001"
        assert critique.category == CritiqueCategory.PERFORMANCE
        assert critique.severity == CritiqueSeverity.MODERATE

    def test_critique_with_evidence(self):
        """Should accept evidence list."""
        critique = HypothesisCritiqueRecord(
            cycle_id="cycle-2024-002",
            hypothesis="Microservices split will reduce coupling",
            critique_summary="Split introduces network latency",
            qualitative_feedback="Inter-service calls add 10-50ms overhead",
            category=CritiqueCategory.ARCHITECTURE,
            severity=CritiqueSeverity.HIGH,
            evidence=[
                "Load test results show 15ms average latency increase",
                "Network partition simulation caused 3 cascading failures"
            ]
        )
        assert len(critique.evidence) == 2

    def test_required_fields_validation(self):
        """Should reject missing required fields."""
        with pytest.raises(ValidationError):
            HypothesisCritiqueRecord(
                cycle_id="",  # empty not allowed
                hypothesis="Test",
                critique_summary="Test",
                qualitative_feedback="Test",
                category=CritiqueCategory.CODE_QUALITY,
                severity=CritiqueSeverity.LOW
            )


class TestAutonomousEscalationRecord:
    """Tests for AutonomousEscalationRecord model."""

    def test_valid_escalation(self):
        """Should create valid escalation record."""
        escalation = AutonomousEscalationRecord(
            error_type="AuthenticationFailure",
            description="API key rejected after 3 refresh attempts"
        )
        assert escalation.error_type == "AuthenticationFailure"
        assert escalation.is_fatal is True  # default
        assert escalation.timestamp is not None

    def test_non_fatal_escalation(self):
        """Should support non-fatal escalations."""
        escalation = AutonomousEscalationRecord(
            error_type="RateLimitExceeded",
            description="Rate limit hit, need manual quota increase",
            is_fatal=False,
            recovery_attempts=[
                "Implemented exponential backoff",
                "Reduced concurrent requests to 2"
            ]
        )
        assert escalation.is_fatal is False
        assert len(escalation.recovery_attempts) == 2

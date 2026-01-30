"""Tests for human_clone module."""
import pytest
from pydantic import ValidationError

from quadracode_contracts.human_clone import (
    HumanCloneExhaustionMode,
    HumanCloneTrigger,
)


class TestHumanCloneExhaustionMode:
    """Tests for HumanCloneExhaustionMode enum."""

    def test_all_modes_exist(self):
        """Should have all expected exhaustion modes."""
        assert HumanCloneExhaustionMode.NONE == "none"
        assert HumanCloneExhaustionMode.CONTEXT_SATURATION == "context_saturation"
        assert HumanCloneExhaustionMode.RETRY_DEPLETION == "retry_depletion"
        assert HumanCloneExhaustionMode.TOOL_BACKPRESSURE == "tool_backpressure"
        assert HumanCloneExhaustionMode.LLM_STOP == "llm_stop"
        assert HumanCloneExhaustionMode.TEST_FAILURE == "test_failure"
        assert HumanCloneExhaustionMode.HYPOTHESIS_EXHAUSTED == "hypothesis_exhausted"
        assert HumanCloneExhaustionMode.PREDICTED_EXHAUSTION == "predicted_exhaustion"

    def test_mode_from_string(self):
        """Should create mode from string value."""
        mode = HumanCloneExhaustionMode("context_saturation")
        assert mode == HumanCloneExhaustionMode.CONTEXT_SATURATION


class TestHumanCloneTrigger:
    """Tests for HumanCloneTrigger model."""

    def test_minimal_trigger(self):
        """Should create trigger with required fields only."""
        trigger = HumanCloneTrigger(
            cycle_iteration=0,
            exhaustion_mode=HumanCloneExhaustionMode.NONE
        )
        assert trigger.cycle_iteration == 0
        assert trigger.exhaustion_mode == HumanCloneExhaustionMode.NONE
        assert trigger.required_artifacts == []
        assert trigger.rationale is None

    def test_full_trigger(self):
        """Should create trigger with all fields."""
        trigger = HumanCloneTrigger(
            cycle_iteration=5,
            exhaustion_mode=HumanCloneExhaustionMode.TEST_FAILURE,
            required_artifacts=[
                "Updated unit tests covering edge cases",
                "Integration test for API boundary",
                "Performance benchmark results"
            ],
            rationale="Test suite regression detected after refactoring data layer"
        )
        assert trigger.cycle_iteration == 5
        assert len(trigger.required_artifacts) == 3
        assert "Performance benchmark" in trigger.required_artifacts[2]

    def test_cycle_iteration_must_be_non_negative(self):
        """cycle_iteration must be >= 0."""
        with pytest.raises(ValidationError):
            HumanCloneTrigger(
                cycle_iteration=-1,
                exhaustion_mode=HumanCloneExhaustionMode.NONE
            )

    def test_artifacts_normalized_from_none(self):
        """None artifacts should become empty list."""
        trigger = HumanCloneTrigger(
            cycle_iteration=0,
            exhaustion_mode=HumanCloneExhaustionMode.NONE,
            required_artifacts=None
        )
        assert trigger.required_artifacts == []

    def test_artifacts_normalized_from_single_value(self):
        """Single string artifact should become list with one item."""
        trigger = HumanCloneTrigger(
            cycle_iteration=1,
            exhaustion_mode=HumanCloneExhaustionMode.RETRY_DEPLETION,
            required_artifacts="Single artifact requirement"
        )
        assert trigger.required_artifacts == ["Single artifact requirement"]

    def test_artifacts_stripped_of_whitespace(self):
        """Artifact strings should be stripped."""
        trigger = HumanCloneTrigger(
            cycle_iteration=2,
            exhaustion_mode=HumanCloneExhaustionMode.CONTEXT_SATURATION,
            required_artifacts=["  padded artifact  ", "another one   "]
        )
        assert trigger.required_artifacts == ["padded artifact", "another one"]

    def test_non_string_artifacts_coerced(self):
        """Non-string artifacts should be coerced to strings."""
        trigger = HumanCloneTrigger(
            cycle_iteration=0,
            exhaustion_mode=HumanCloneExhaustionMode.NONE,
            required_artifacts=[123, 45.67, True]
        )
        assert trigger.required_artifacts == ["123", "45.67", "True"]

    def test_context_saturation_mode(self):
        """Should handle context saturation scenario."""
        trigger = HumanCloneTrigger(
            cycle_iteration=100,
            exhaustion_mode=HumanCloneExhaustionMode.CONTEXT_SATURATION,
            required_artifacts=["Summarized context checkpoint"],
            rationale="Context window exceeded 80% capacity after document ingestion"
        )
        assert trigger.exhaustion_mode == HumanCloneExhaustionMode.CONTEXT_SATURATION

    def test_hypothesis_exhausted_mode(self):
        """Should handle hypothesis exhaustion scenario."""
        trigger = HumanCloneTrigger(
            cycle_iteration=25,
            exhaustion_mode=HumanCloneExhaustionMode.HYPOTHESIS_EXHAUSTED,
            required_artifacts=[
                "Alternative approach proposal",
                "Root cause analysis document"
            ],
            rationale="All 5 proposed optimization hypotheses failed validation"
        )
        assert trigger.exhaustion_mode == HumanCloneExhaustionMode.HYPOTHESIS_EXHAUSTED
        assert len(trigger.required_artifacts) == 2

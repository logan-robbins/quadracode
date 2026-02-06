"""
Data contracts governing the interaction between the orchestrator and the
HumanClone — a specialized component responsible for simulating human-like
interventions and feedback.

These contracts are crucial for the "Plan-Refine-Play" (PRP) loop.  They
provide a structured way for the HumanClone to signal different types of
"exhaustion" (e.g., context saturation, retry depletion), which in turn
guides the orchestrator's planning and recovery strategies.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class HumanCloneExhaustionMode(str, Enum):
    """Exhaustion modes recognized by the HumanClone protocol.

    Each mode classifies the reason for a HumanClone intervention, providing
    the orchestrator with the context needed to plan its next actions.  Every
    mode corresponds to a specific type of failure or deadlock requiring a
    strategic adjustment.
    """

    NONE = "none"
    CONTEXT_SATURATION = "context_saturation"
    RETRY_DEPLETION = "retry_depletion"
    TOOL_BACKPRESSURE = "tool_backpressure"
    LLM_STOP = "llm_stop"
    TEST_FAILURE = "test_failure"
    HYPOTHESIS_EXHAUSTED = "hypothesis_exhausted"
    PREDICTED_EXHAUSTION = "predicted_exhaustion"


def _coerce_artifact(value: object) -> str:
    """Coerce an arbitrary value to a stripped artifact string."""
    if isinstance(value, str):
        return value.strip()
    return str(value)


class HumanCloneTrigger(BaseModel):
    """Structured payload emitted by the HumanClone to drive PRP state
    transitions.

    This model is the primary communication mechanism from the HumanClone to
    the orchestrator.  It includes the exhaustion mode, required artifacts
    that the orchestrator must produce, and a rationale for the intervention.
    """

    cycle_iteration: int = Field(
        ...,
        ge=0,
        description="Zero-indexed cycle iteration the trigger pertains to.",
    )
    exhaustion_mode: HumanCloneExhaustionMode = Field(
        ...,
        description="Exhaustion classification guiding recovery strategy.",
    )
    required_artifacts: list[str] = Field(
        default_factory=list,
        description="Concrete artifacts the orchestrator must produce before resubmitting.",
    )
    rationale: str | None = Field(
        default=None,
        description="Optional free-form explanation to aid orchestrator planning.",
    )

    @field_validator("required_artifacts", mode="before")
    @classmethod
    def _normalise_artifacts(cls, value: object) -> list[str]:
        """Normalize *required_artifacts* before validation.

        Ensures the field is always a ``list[str]``, even when the input is
        a single value or ``None``.
        """
        if value is None:
            return []
        if isinstance(value, list):
            return [_coerce_artifact(item) for item in value]
        return [_coerce_artifact(value)]


__all__ = ["HumanCloneTrigger", "HumanCloneExhaustionMode"]

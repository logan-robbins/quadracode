"""
This module defines the data contracts that govern the interaction between the 
orchestrator and the HumanClone, a specialized component responsible for 
simulating human-like interventions and feedback.

These contracts are crucial for the "Plan-Refine-Play" (PRP) loop, a core 
process in the Quadracode system. They provide a structured way for the 
HumanClone to signal different types of "exhaustion" (e.g., context saturation, 
retry depletion), which in turn guides the orchestrator's planning and recovery 
strategies.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class HumanCloneExhaustionMode(str, Enum):
    """
    Enumeration of the exhaustion modes recognized by the HumanClone protocol.

    These modes are used to classify the reason for a HumanClone intervention, 
    providing the orchestrator with the necessary context to plan its next 
    actions. Each mode corresponds to a specific type of failure or deadlock 
    that requires a strategic adjustment.
    """

    NONE = "none"
    CONTEXT_SATURATION = "context_saturation"
    RETRY_DEPLETION = "retry_depletion"
    TOOL_BACKPRESSURE = "tool_backpressure"
    LLM_STOP = "llm_stop"
    TEST_FAILURE = "test_failure"
    HYPOTHESIS_EXHAUSTED = "hypothesis_exhausted"
    PREDICTED_EXHAUSTION = "predicted_exhaustion"


class HumanCloneTrigger(BaseModel):
    """
    Represents the structured payload emitted by the HumanClone to drive state 
    transitions in the PRP (Plan-Refine-Play) loop.

    This model is the primary communication mechanism from the HumanClone to the 
    orchestrator. It includes the exhaustion mode, any required artifacts that 
    the orchestrator must produce, and a rationale for the intervention. This 
    structured data is essential for the orchestrator to effectively adapt its 
    plan.
    """

    cycle_iteration: int = Field(
        ...,
        ge=0,
        description="Zero-indexed cycle iteration the trigger pertains to.",
    )
    exhaustion_mode: HumanCloneExhaustionMode = Field(
        ...,
        description="Exhaustion classification guiding the orchestrator's recovery strategy.",
    )
    required_artifacts: List[str] = Field(
        default_factory=list,
        description="Concrete artifacts the orchestrator must produce before resubmitting.",
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Optional free-form explanation to aid orchestrator planning.",
    )

    @staticmethod
    def _coerce_artifact(value: object) -> str:
        """Coerces a value to a stripped string."""
        if isinstance(value, str):
            return value.strip()
        return str(value)

    @field_validator("required_artifacts", mode="before")
    @classmethod
    def _normalise_artifacts(cls, value: object) -> List[str]:
        """
        Normalizes the `required_artifacts` field before validation.
        
        This validator ensures that the `required_artifacts` field is always a 
        list of strings, even if the input is a single value or `None`.
        """
        if value is None:
            return []
        if isinstance(value, list):
            return [cls._coerce_artifact(item) for item in value]
        return [cls._coerce_artifact(value)]


__all__ = ["HumanCloneTrigger", "HumanCloneExhaustionMode"]

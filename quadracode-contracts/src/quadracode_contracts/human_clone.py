"""Contracts governing orchestrator ↔ HumanClone trigger exchanges."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class HumanCloneExhaustionMode(str, Enum):
    """Enumeration of exhaustion modes recognised by the HumanClone protocol."""

    NONE = "none"
    CONTEXT_SATURATION = "context_saturation"
    RETRY_DEPLETION = "retry_depletion"
    TOOL_BACKPRESSURE = "tool_backpressure"
    LLM_STOP = "llm_stop"
    TEST_FAILURE = "test_failure"
    HYPOTHESIS_EXHAUSTED = "hypothesis_exhausted"
    PREDICTED_EXHAUSTION = "predicted_exhaustion"


class HumanCloneTrigger(BaseModel):
    """Structured payload emitted by the HumanClone to drive PRP transitions."""

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
        if isinstance(value, str):
            return value.strip()
        return str(value)

    @field_validator("required_artifacts", mode="before")
    @classmethod
    def _normalise_artifacts(cls, value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [cls._coerce_artifact(item) for item in value]
        return [cls._coerce_artifact(value)]


__all__ = ["HumanCloneTrigger", "HumanCloneExhaustionMode"]

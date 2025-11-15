"""
This module defines the data contracts that govern the behavior of the 
Quadracode system when operating in autonomous mode.

These Pydantic models are used to structure the communication and state 
management required for autonomous orchestration. They cover routing directives, 
progress checkpoints, hypothesis critiques, and escalation procedures. By 
enforcing a strict schema for these interactions, this module ensures that the 
autonomous workflow is robust, predictable, and transparent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional, Literal

from pydantic import BaseModel, Field, ValidationError, ConfigDict


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AutonomousRoutingDirective(BaseModel):
    """
    Defines the routing instructions emitted by the autonomous orchestrator.

    This model is used to signal how a particular message or result should be 
    handled, such as whether it needs to be delivered to a human for review or 
    if a fatal error requires escalation. It provides a structured way for the 
    orchestrator to control the flow of information in the system.
    """

    model_config = ConfigDict(extra="ignore")

    deliver_to_human: bool = Field(
        default=False,
        description="Whether the orchestrator intends to notify the human (e.g., final report).",
    )
    escalate: bool = Field(
        default=False,
        description="Flag indicating a fatal error that must be escalated to the human.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Short description explaining why human delivery/escalation is requested.",
    )
    recovery_attempts: List[str] = Field(
        default_factory=list,
        description="List of recovery attempts taken before requesting escalation.",
    )

    @classmethod
    def from_payload(
        cls,
        payload: Any,
    ) -> Optional["AutonomousRoutingDirective"]:
        """
        Safely creates a directive from an arbitrary payload.

        This class method is designed to be resilient to malformed or 
        unstructured data. It attempts to parse a dictionary-like payload into 
        a strongly-typed `AutonomousRoutingDirective`, filtering out any 
        unrecognized fields.

        Args:
            payload: The raw payload to parse.

        Returns:
            An instance of `AutonomousRoutingDirective`, or `None` if the 
            payload is not a valid directive.
        """

        if not isinstance(payload, dict):
            return None

        try:
            return cls(**payload)
        except ValidationError:
            filtered: dict[str, Any] = {}
            for field in ("deliver_to_human", "escalate", "reason", "recovery_attempts"):
                if field in payload:
                    filtered[field] = payload[field]

            if not filtered:
                return None

            try:
                return cls(**filtered)
            except ValidationError:
                return None

    def to_payload(self) -> dict[str, Any]:
        """
        Serializes the directive to a JSON-compatible dictionary.

        This method is used to prepare the directive for inclusion in a message 
        envelope.

        Returns:
            A dictionary representation of the directive.
        """

        return self.dict()


class AutonomousCheckpointRecord(BaseModel):
    """
    Represents a structured checkpoint entry recorded during autonomous 
    operation.

    Checkpoints are used to track the progress of a long-running autonomous 
    task. They provide a snapshot of the task's status at a given milestone, 
    including a summary of the work completed and the planned next steps.
    """

    milestone: int = Field(..., ge=1)
    status: Literal["in_progress", "complete", "blocked"]
    summary: str = Field(..., min_length=1)
    next_steps: List[str] = Field(default_factory=list)
    title: Optional[str] = Field(
        default=None,
        description="Optional human-readable milestone title.",
    )
    recorded_at: str = Field(default_factory=_utc_now)


class CritiqueCategory(str, Enum):
    """
    Defines the categorization buckets for hypothesis critiques.

    This enumeration is used to classify critiques based on the aspect of the 
    system they are addressing.
    """

    CODE_QUALITY = "code_quality"
    ARCHITECTURE = "architecture"
    TEST_COVERAGE = "test_coverage"
    PERFORMANCE = "performance"


class CritiqueSeverity(str, Enum):
    """
    Defines the severity rankings used to prioritize critiques.

    This enumeration allows for the classification of critiques based on their 
    urgency and impact.
    """

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class HypothesisCritiqueRecord(BaseModel):
    """
    Represents a structured critique of a refinement hypothesis.

    This model is used to capture detailed feedback on a proposed change or 
    hypothesis. It includes a summary of the critique, qualitative feedback, 
    and a categorization based on severity and area of concern.
    """

    cycle_id: str = Field(..., min_length=1)
    hypothesis: str = Field(..., min_length=1)
    critique_summary: str = Field(..., min_length=1)
    qualitative_feedback: str = Field(..., min_length=1)
    category: CritiqueCategory
    severity: CritiqueSeverity
    evidence: List[str] = Field(default_factory=list)
    recorded_at: str = Field(default_factory=_utc_now)


class AutonomousEscalationRecord(BaseModel):
    """

    Represents the data captured when an autonomous process needs to escalate 
    an issue to a human.

    This model is used to create a detailed record of an escalation event, 
    including the type of error, a description of the problem, and a list of 
    any recovery attempts that were made.
    """

    error_type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    recovery_attempts: List[str] = Field(default_factory=list)
    is_fatal: bool = Field(default=True)
    timestamp: str = Field(default_factory=_utc_now)


__all__ = [
    "AutonomousRoutingDirective",
    "AutonomousCheckpointRecord",
    "CritiqueCategory",
    "CritiqueSeverity",
    "HypothesisCritiqueRecord",
    "AutonomousEscalationRecord",
]

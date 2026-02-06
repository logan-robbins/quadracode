"""
Data contracts governing the behaviour of the Quadracode system when operating
in autonomous mode.

These Pydantic models structure the communication and state management required
for autonomous orchestration — routing directives, progress checkpoints,
hypothesis critiques, and escalation procedures.  The strict schemas ensure
that the autonomous workflow is robust, predictable, and transparent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError


def _utc_now() -> str:
    """Return the current UTC time as a seconds-precision ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Fields common to the routing directive that are safe to extract from an
# untrusted payload during the two-pass parse in ``from_payload``.
# ---------------------------------------------------------------------------
_ROUTING_KNOWN_FIELDS: frozenset[str] = frozenset(
    {"deliver_to_human", "escalate", "reason", "recovery_attempts"}
)


class AutonomousRoutingDirective(BaseModel):
    """Routing instructions emitted by the autonomous orchestrator.

    Signals how a particular message or result should be handled — e.g.
    delivered to a human for review, or escalated due to a fatal error.
    """

    model_config = ConfigDict(extra="ignore")

    deliver_to_human: bool = Field(
        default=False,
        description="Whether the orchestrator intends to notify the human.",
    )
    escalate: bool = Field(
        default=False,
        description="Flag indicating a fatal error that must be escalated.",
    )
    reason: str | None = Field(
        default=None,
        description="Short description of why delivery/escalation is requested.",
    )
    recovery_attempts: list[str] = Field(
        default_factory=list,
        description="Recovery attempts taken before requesting escalation.",
    )

    @classmethod
    def from_payload(cls, payload: Any) -> Self | None:
        """Safely create a directive from an arbitrary payload.

        Resilient to malformed or unstructured data.  A two-pass approach is
        used: first the full dict is tried (with ``extra='ignore'``), then —
        if that fails — only recognised fields are extracted and retried.

        Args:
            payload: The raw payload to parse.

        Returns:
            An :class:`AutonomousRoutingDirective`, or ``None`` if the
            payload cannot be interpreted as a valid directive.
        """
        if not isinstance(payload, dict):
            return None

        # Pass 1: try full dict (extra fields are silently dropped).
        try:
            return cls(**payload)
        except ValidationError:
            pass

        # Pass 2: extract only known fields and retry.
        filtered = {k: v for k, v in payload.items() if k in _ROUTING_KNOWN_FIELDS}
        if not filtered:
            return None

        try:
            return cls(**filtered)
        except ValidationError:
            return None

    def to_payload(self) -> dict[str, Any]:
        """Serialize the directive to a JSON-compatible dictionary.

        Returns:
            A dictionary representation of the directive.
        """
        return self.model_dump()


class AutonomousCheckpointRecord(BaseModel):
    """Structured checkpoint entry recorded during autonomous operation.

    Checkpoints track the progress of a long-running autonomous task,
    providing a snapshot of status at a given milestone including work
    completed and planned next steps.
    """

    milestone: int = Field(..., ge=1)
    status: Literal["in_progress", "complete", "blocked"]
    summary: str = Field(..., min_length=1)
    next_steps: list[str] = Field(default_factory=list)
    title: str | None = Field(
        default=None,
        description="Optional human-readable milestone title.",
    )
    recorded_at: str = Field(default_factory=_utc_now)


class CritiqueCategory(str, Enum):
    """Categorization buckets for hypothesis critiques."""

    CODE_QUALITY = "code_quality"
    ARCHITECTURE = "architecture"
    TEST_COVERAGE = "test_coverage"
    PERFORMANCE = "performance"


class CritiqueSeverity(str, Enum):
    """Severity rankings used to prioritize critiques."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class HypothesisCritiqueRecord(BaseModel):
    """Structured critique of a refinement hypothesis.

    Captures detailed feedback on a proposed change or hypothesis, including
    a summary, qualitative feedback, and categorization by severity and
    area of concern.
    """

    cycle_id: str = Field(..., min_length=1)
    hypothesis: str = Field(..., min_length=1)
    critique_summary: str = Field(..., min_length=1)
    qualitative_feedback: str = Field(..., min_length=1)
    category: CritiqueCategory
    severity: CritiqueSeverity
    evidence: list[str] = Field(default_factory=list)
    recorded_at: str = Field(default_factory=_utc_now)


class AutonomousEscalationRecord(BaseModel):
    """Data captured when an autonomous process escalates to a human.

    Creates a detailed record of the escalation event — error type,
    description, and any recovery attempts that were made.
    """

    error_type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    recovery_attempts: list[str] = Field(default_factory=list)
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

"""Contracts for HUMAN_OBSOLETE autonomous mode routing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional, Literal

from pydantic import BaseModel, Field, ValidationError, ConfigDict


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AutonomousRoutingDirective(BaseModel):
    """Routing instructions emitted during autonomous orchestration."""

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
        """Create a directive from an arbitrary payload value."""

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
        """Return a JSON-serialisable payload for message envelopes."""

        return self.dict()


class AutonomousCheckpointRecord(BaseModel):
    """Structured checkpoint entry for HUMAN_OBSOLETE autonomous mode."""

    milestone: int = Field(..., ge=1)
    status: Literal["in_progress", "complete", "blocked"]
    summary: str = Field(..., min_length=1)
    next_steps: List[str] = Field(default_factory=list)
    title: Optional[str] = Field(
        default=None,
        description="Optional human-readable milestone title.",
    )
    recorded_at: str = Field(default_factory=_utc_now)


class AutonomousCritiqueRecord(BaseModel):
    """Self-critique entry emitted during autonomous execution."""

    action_taken: str = Field(..., min_length=1)
    outcome: str = Field(..., min_length=1)
    quality_assessment: Literal["good", "adequate", "poor"]
    improvements: List[str] = Field(default_factory=list)
    recorded_at: str = Field(default_factory=_utc_now)


class AutonomousEscalationRecord(BaseModel):
    """Escalation event data captured when contacting a human."""

    error_type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    recovery_attempts: List[str] = Field(default_factory=list)
    is_fatal: bool = Field(default=True)
    timestamp: str = Field(default_factory=_utc_now)


__all__ = [
    "AutonomousRoutingDirective",
    "AutonomousCheckpointRecord",
    "AutonomousCritiqueRecord",
    "AutonomousEscalationRecord",
]

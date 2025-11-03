from __future__ import annotations

import json
from typing import List, Optional, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from quadracode_contracts import (
    AutonomousCheckpointRecord,
    AutonomousCritiqueRecord,
    AutonomousEscalationRecord,
    AutonomousRoutingDirective,
)


class AutonomousCheckpointRequest(BaseModel):
    """Schema for recording autonomous milestone checkpoints."""

    milestone: int = Field(..., ge=1)
    status: Literal["in_progress", "complete", "blocked"]
    summary: str = Field(..., min_length=1)
    next_steps: List[str] = Field(default_factory=list)
    title: Optional[str] = Field(
        default=None,
        description="Optional human-readable milestone name.",
    )


class AutonomousEscalationRequest(BaseModel):
    """Schema for escalation events to the supervising human."""

    error_type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    recovery_attempts: List[str] = Field(default_factory=list)
    is_fatal: bool = Field(
        default=True,
        description="Whether this error is fatal and requires human intervention.",
    )


class AutonomousCritiqueRequest(BaseModel):
    """Schema for self-critique events emitted by autonomous orchestrator."""

    action_taken: str = Field(..., min_length=1)
    outcome: str = Field(..., min_length=1)
    quality_assessment: Literal["good", "adequate", "poor"]
    improvements: List[str] = Field(default_factory=list)


def _format_output(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


@tool(args_schema=AutonomousCheckpointRequest)
def autonomous_checkpoint(
    milestone: int,
    status: str,
    summary: str,
    next_steps: List[str] | None = None,
    title: str | None = None,
) -> str:
    """Record an autonomous milestone checkpoint for HUMAN_OBSOLETE mode."""

    record = AutonomousCheckpointRecord(
        milestone=milestone,
        status=status,  # type: ignore[arg-type]
        summary=summary,
        next_steps=next_steps or [],
        title=title,
    )
    return _format_output(
        {
            "event": "checkpoint",
            "record": record.dict(),
        }
    )


@tool(args_schema=AutonomousEscalationRequest)
def autonomous_escalate(
    error_type: str,
    description: str,
    recovery_attempts: List[str] | None = None,
    is_fatal: bool = True,
) -> str:
    """Request human escalation if a fatal error blocks progress."""

    payload = AutonomousEscalationRequest(
        error_type=error_type,
        description=description,
        recovery_attempts=recovery_attempts or [],
        is_fatal=is_fatal,
    )

    if not payload.is_fatal:
        return _format_output(
            {
                "event": "escalation",
                "status": "dismissed",
                "message": "Error is not fatal. Continue autonomous recovery.",
            }
        )

    record = AutonomousEscalationRecord(
        error_type=payload.error_type,
        description=payload.description,
        recovery_attempts=payload.recovery_attempts,
        is_fatal=True,
    )
    routing = AutonomousRoutingDirective(
        deliver_to_human=True,
        escalate=True,
        reason=payload.description,
        recovery_attempts=payload.recovery_attempts,
    )
    return _format_output(
        {
            "event": "escalation",
            "status": "escalate",
            "record": record.dict(),
            "routing": routing.to_payload(),
        }
    )


@tool(args_schema=AutonomousCritiqueRequest)
def autonomous_critique(
    action_taken: str,
    outcome: str,
    quality_assessment: str,
    improvements: List[str] | None = None,
) -> str:
    """Log a self-critique entry for continuous quality evaluation."""

    record = AutonomousCritiqueRecord(
        action_taken=action_taken,
        outcome=outcome,
        quality_assessment=quality_assessment,  # type: ignore[arg-type]
        improvements=improvements or [],
    )
    return _format_output(
        {
            "event": "critique",
            "record": record.dict(),
        }
    )

from __future__ import annotations

import json
from typing import List, Optional, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from quadracode_contracts import (
    AutonomousCheckpointRecord,
    AutonomousEscalationRecord,
    AutonomousRoutingDirective,
    HypothesisCritiqueRecord,
)

from .test_suite import execute_full_test_suite


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


class HypothesisCritiqueRequest(BaseModel):
    """Schema for hypothesis-centric self-critiques."""

    cycle_id: str = Field(..., min_length=1)
    hypothesis: str = Field(..., min_length=1)
    critique_summary: str = Field(..., min_length=1)
    qualitative_feedback: str = Field(..., min_length=1)
    category: Literal["code_quality", "architecture", "test_coverage", "performance"]
    severity: Literal["low", "moderate", "high", "critical"]
    evidence: List[str] = Field(default_factory=list)


class FinalReviewRequest(BaseModel):
    """Structured final review submission that enforces test execution."""

    summary: str = Field(..., min_length=1)
    recovery_attempts: List[str] = Field(default_factory=list)
    artifacts: List[str] = Field(
        default_factory=list,
        description="Artifacts that should be highlighted for HumanClone review.",
    )
    workspace_root: Optional[str] = Field(
        default=None,
        description="Workspace root override for the test suite tool.",
    )
    include_e2e: bool = Field(
        default=True,
        description="Run end-to-end suites if discovery finds them.",
    )
    coverage_goal: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Optional coverage threshold to assert before final review.",
    )


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


@tool(args_schema=HypothesisCritiqueRequest)
def hypothesis_critique(
    cycle_id: str,
    hypothesis: str,
    critique_summary: str,
    qualitative_feedback: str,
    category: str,
    severity: str,
    evidence: List[str] | None = None,
) -> str:
    """Capture a structured critique tied to a specific refinement hypothesis."""

    record = HypothesisCritiqueRecord(
        cycle_id=cycle_id,
        hypothesis=hypothesis,
        critique_summary=critique_summary,
        qualitative_feedback=qualitative_feedback,
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        evidence=evidence or [],
    )
    return _format_output(
        {
            "event": "hypothesis_critique",
            "record": record.dict(),
        }
    )


@tool(args_schema=FinalReviewRequest)
def request_final_review(
    summary: str,
    recovery_attempts: List[str] | None = None,
    artifacts: List[str] | None = None,
    workspace_root: str | None = None,
    include_e2e: bool = True,
    coverage_goal: float | None = None,
) -> str:
    """Submit work for HumanClone review only after a full test suite passes."""

    tests = execute_full_test_suite(
        workspace_root=workspace_root,
        include_e2e=include_e2e,
    )
    overall_status = str(tests.get("overall_status") or "").lower()
    if overall_status != "passed":
        return _format_output(
            {
                "event": "tests_failed",
                "message": "Full test suite must pass before requesting final review.",
                "tests": tests,
            }
        )

    record = AutonomousEscalationRecord(
        error_type="final_review",
        description=summary,
        recovery_attempts=recovery_attempts or [],
        is_fatal=False,
    )
    payload: dict[str, object] = {
        "event": "final_review_request",
        "record": record.dict(),
        "tests": tests,
        "artifacts": artifacts or [],
    }

    coverage_data = tests.get("coverage") if isinstance(tests, dict) else None
    if coverage_goal is not None and isinstance(coverage_data, dict):
        coverage_min = coverage_data.get("min")
        if isinstance(coverage_min, (int, float)):
            payload["coverage_goal_met"] = coverage_min >= coverage_goal

    return _format_output(payload)

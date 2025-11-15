"""Provides a structured tool for managing the Quadracode Refinement Ledger.

This module defines a LangChain tool that allows agents to interact with the
Plan-Replan-Propose (PRP) refinement ledger. The ledger is a critical component
for meta-cognition, enabling agents to track hypotheses, record outcomes, and
query past failures to inform future strategies. By structuring these operations
as a tool, agents can autonomously manage their learning cycles, propose new
problem-solving approaches, and build causal chains to understand complex
failure modes.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Union

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

OperationLiteral = Literal[
    "propose_hypothesis",
    "conclude_hypothesis",
    "query_past_failures",
    "infer_causal_chain",
]


class ManageRefinementLedgerRequest(BaseModel):
    """Schema for structured refinement ledger operations, ensuring valid requests.

    This Pydantic model defines the contract for all interactions with the refinement
    ledger. It specifies the supported operations (`propose_hypothesis`,
    `conclude_hypothesis`, `query_past_failures`, `infer_causal_chain`) and enforces
    the presence of required fields for each. For example, proposing a hypothesis
    requires a non-empty `hypothesis` description, while concluding one requires a
    `cycle_id`, `status`, and `summary`. This validation prevents malformed events
    from being dispatched to the runtime's metrics and state management systems.
    """

    operation: OperationLiteral
    hypothesis: Optional[str] = Field(
        default=None,
        description="Description of the new hypothesis (required for propose).",
    )
    strategy: Optional[str] = Field(
        default=None,
        description="Technique or plan that differentiates this hypothesis from previous attempts.",
    )
    summary: Optional[str] = Field(
        default=None,
        description="Status/summary text. Required when concluding a hypothesis.",
    )
    status: Optional[Literal["succeeded", "failed", "abandoned", "in_progress"]] = Field(
        default=None,
        description="Outcome label when concluding a hypothesis.",
    )
    cycle_id: Optional[str] = Field(
        default=None,
        description="Existing ledger cycle identifier to update (for conclude).",
    )
    dependencies: List[Union[str, int]] = Field(
        default_factory=list,
        description="Cycle identifiers that this hypothesis depends on.",
    )
    filter: Optional[str] = Field(
        default=None,
        description="Keyword filter applied during failure queries.",
    )
    limit: Optional[int] = Field(
        default=5,
        ge=1,
        le=25,
        description="Maximum number of rows returned for failure queries.",
    )
    include_tests: bool = Field(
        default=False,
        description="When querying, include detailed test metadata in results.",
    )
    cycle_ids: List[Union[str, int]] = Field(
        default_factory=list,
        description="Specific cycles to inspect when inferring causal chains.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form metadata attached to the request for downstream analytics.",
    )

    @model_validator(mode="after")
    def _validate_operation_specific_fields(self) -> "ManageRefinementLedgerRequest":
        if self.operation == "propose_hypothesis":
            if not (self.hypothesis and self.hypothesis.strip()):
                raise ValueError("hypothesis is required when proposing")
        if self.operation == "conclude_hypothesis":
            if not (self.cycle_id and self.cycle_id.strip()):
                raise ValueError("cycle_id is required when concluding a hypothesis")
            if not (self.status and self.status.strip()):
                raise ValueError("status is required when concluding a hypothesis")
            if not (self.summary and self.summary.strip()):
                raise ValueError("summary is required when concluding a hypothesis")
        if self.operation == "query_past_failures":
            if self.limit is None:
                self.limit = 5
        if self.operation == "infer_causal_chain":
            if not self.cycle_ids:
                raise ValueError("cycle_ids must be provided when inferring causal chains")
        return self


def _normalize_identifiers(values: List[Union[str, int]]) -> List[str]:
    """Cleans and standardizes a list of cycle identifiers.

    Ensures that all identifiers are strings and removes any empty or whitespace-only values.
    """
    normalized: List[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized


def _format_payload(payload: Dict[str, Any]) -> str:
    """Serializes the final event payload to a consistent JSON string format."""
    return json.dumps(payload, indent=2, sort_keys=True)


@tool(args_schema=ManageRefinementLedgerRequest)
def manage_refinement_ledger(**payload: Any) -> str:  # type: ignore[override]
    """Dispatches a structured event to the PRP refinement ledger for meta-cognition.

    This tool enables an agent to engage in a structured learning process by
    managing hypotheses about how to achieve its goals. It translates a validated
    `ManageRefinementLedgerRequest` into a JSON payload that the Quadracode runtime
    can process. The runtime uses these events to update the agent's internal state,
    record metrics for observability, and inform the Deliberative Planner.

    Operations:
    - `propose_hypothesis`: Records a new problem-solving strategy, linking it to
      dependencies.
    - `conclude_hypothesis`: Updates a hypothesis with a terminal status (`succeeded`,
      `failed`, `abandoned`).
    - `query_past_failures`: Retrieves historical failure data to inform new
      hypotheses.
    - `infer_causal_chain`: Asks the runtime to analyze a sequence of cycles to
      identify root causes of failure.
    """

    request = ManageRefinementLedgerRequest(**payload)
    dependencies = _normalize_identifiers(request.dependencies)
    cycle_ids = _normalize_identifiers(request.cycle_ids)

    base_payload: Dict[str, Any] = {
        "event": "refinement_ledger",
        "operation": request.operation,
        "metadata": request.metadata,
    }

    if request.operation == "propose_hypothesis":
        base_payload.update(
            {
                "hypothesis": request.hypothesis.strip(),
                "strategy": (request.strategy or "").strip() or None,
                "summary": (request.summary or "pending evaluation").strip(),
                "dependencies": dependencies,
            }
        )
    elif request.operation == "conclude_hypothesis":
        base_payload.update(
            {
                "cycle_id": request.cycle_id.strip(),
                "status": request.status.strip(),
                "summary": request.summary.strip(),
            }
        )
    elif request.operation == "query_past_failures":
        base_payload.update(
            {
                "filter": (request.filter or "").strip() or None,
                "limit": request.limit,
                "include_tests": request.include_tests,
            }
        )
    elif request.operation == "infer_causal_chain":
        base_payload.update(
            {
                "cycle_ids": cycle_ids,
            }
        )

    if request.operation in {"propose_hypothesis", "conclude_hypothesis"} and dependencies:
        base_payload["dependencies"] = dependencies

    return _format_payload(base_payload)


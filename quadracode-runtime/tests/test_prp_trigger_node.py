import asyncio
import json
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from quadracode_contracts import (
    HUMAN_CLONE_RECIPIENT,
    MessageEnvelope,
)
from quadracode_runtime.nodes.prp_trigger import prp_trigger_check
from quadracode_runtime.prp import parse_human_clone_trigger
from quadracode_runtime.state import (
    ExhaustionMode,
    PRPState,
    RefinementLedgerEntry,
    make_initial_context_engine_state,
    add_refinement_ledger_entry,
)
from quadracode_runtime.validation import validate_supervisor_envelope


def _make_trigger_payload() -> dict[str, object]:
    return {
        "cycle_iteration": 3,
        "exhaustion_mode": "test_failure",
        "required_artifacts": ["unit_test_report", "coverage_summary"],
        "rationale": "Tests still failing on edge cases.",
    }


def test_parse_human_clone_trigger_from_json() -> None:
    payload = json.dumps(_make_trigger_payload())
    trigger = parse_human_clone_trigger(payload)
    assert trigger.cycle_iteration == 3
    assert trigger.exhaustion_mode.value == "test_failure"
    assert trigger.required_artifacts == ["unit_test_report", "coverage_summary"]


def test_prp_trigger_check_converts_message_to_tool_event() -> None:
    state = make_initial_context_engine_state()
    state["prp_state"] = PRPState.PROPOSE
    add_refinement_ledger_entry(
        state,
        RefinementLedgerEntry(
            cycle_id="cycle-1",
            timestamp=datetime.now(timezone.utc),
            hypothesis="Stabilise failing tests",
            status="proposed",
            outcome_summary="pending",
        ),
    )
    state["_last_envelope_sender"] = HUMAN_CLONE_RECIPIENT
    message = HumanMessage(content=json.dumps(_make_trigger_payload()))
    state["messages"].append(message)

    updated = asyncio.run(prp_trigger_check(state))

    assert updated["prp_state"] == PRPState.HYPOTHESIZE
    assert updated["exhaustion_mode"] == ExhaustionMode.TEST_FAILURE
    assert updated["supervisor_requirements"] == ["unit_test_report", "coverage_summary"]

    assert isinstance(updated["messages"][-2], SystemMessage)
    summary_message = updated["messages"][-2]
    assert "Supervisor Review Feedback" in summary_message.content

    tool_message = updated["messages"][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.name == "hypothesis_critique"
    assert updated["error_history"], "Critique events should populate error history."
    assert updated["critique_backlog"], "Critique backlog should track translation output."


def test_validate_supervisor_envelope_rejects_bad_payload() -> None:
    envelope = MessageEnvelope(
        sender=HUMAN_CLONE_RECIPIENT,
        recipient="orchestrator",
        message="please keep going",
        payload={},
    )
    valid, feedback = validate_supervisor_envelope(envelope)
    assert not valid
    assert feedback is not None
    assert feedback.recipient == HUMAN_CLONE_RECIPIENT
    assert "schema_error" in feedback.payload


def test_validate_supervisor_envelope_allows_valid_payload() -> None:
    envelope = MessageEnvelope(
        sender=HUMAN_CLONE_RECIPIENT,
        recipient="orchestrator",
        message=json.dumps(_make_trigger_payload()),
        payload={},
    )
    valid, feedback = validate_supervisor_envelope(envelope)
    assert valid
    assert feedback is None

import json
from datetime import datetime, timezone

from langchain_core.messages import ToolMessage

from quadracode_runtime.autonomous import process_autonomous_tool_response
from quadracode_runtime.state import (
    RefinementLedgerEntry,
    add_refinement_ledger_entry,
    make_initial_context_engine_state,
)


def _tool_message(payload: dict[str, object]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload),
        name="hypothesis_critique",
        tool_call_id="test-critique",
    )


def test_hypothesis_critique_translation_updates_state() -> None:
    state = make_initial_context_engine_state()
    add_refinement_ledger_entry(
        state,
        RefinementLedgerEntry(
            cycle_id="cycle-42",
            timestamp=datetime.now(timezone.utc),
            hypothesis="Introduce resilient caching layer",
            status="proposed",
            outcome_summary="pending",
        ),
    )

    payload = {
        "event": "hypothesis_critique",
        "record": {
            "cycle_id": "cycle-42",
            "hypothesis": "Introduce resilient caching layer",
            "critique_summary": "Cache invalidation remains unsafe",
            "qualitative_feedback": "Requests after deployment return stale entries; add eviction tests and dependency graphs.",
            "category": "architecture",
            "severity": "high",
            "evidence": ["stale_cache.log"],
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }

    updated_state, event = process_autonomous_tool_response(state, _tool_message(payload))

    assert event is not None
    translation = event["payload"]["translation"]
    assert translation["tests"], "Translation should yield test plans"
    assert translation["improvements"], "Translation should yield improvement directives"

    ledger_entry = updated_state["refinement_ledger"][0]
    assert ledger_entry.metadata["critiques"], "Ledger should track critiques in metadata"
    backlog = updated_state["critique_backlog"]
    assert backlog, "Critique backlog should record translated work"
    assert backlog[0]["cycle_id"] == "cycle-42"

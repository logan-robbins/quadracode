from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain_core.messages import ToolMessage

from quadracode_runtime.ledger import process_manage_refinement_ledger_tool_response
from quadracode_runtime.state import RefinementLedgerEntry, make_initial_context_engine_state


def _tool_message(payload: dict[str, object]) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload),
        name="manage_refinement_ledger",
        tool_call_id=payload.get("operation", "ledger-op"),
    )


def test_propose_hypothesis_adds_entry_with_signals() -> None:
    state = make_initial_context_engine_state(context_window_max=512)
    payload = {
        "operation": "propose_hypothesis",
        "hypothesis": "Improve caching layer latency",
        "strategy": "introduce redis pipeline",
        "summary": "Plan cache coherency fixes",
    }

    updated_state, event = process_manage_refinement_ledger_tool_response(state, _tool_message(payload))

    ledger = updated_state.get("refinement_ledger", [])
    assert len(ledger) == 1
    entry = ledger[0]
    assert entry.strategy == "introduce redis pipeline"
    assert entry.novelty_score is not None
    assert entry.predicted_success_probability is not None
    assert event is not None and event["event"] == "refinement_ledger_proposed"
    assert updated_state["messages"][-1].additional_kwargs["source"] == "refinement_ledger"


def test_novelty_guard_blocks_duplicate_without_strategy() -> None:
    state = make_initial_context_engine_state(context_window_max=512)
    existing = RefinementLedgerEntry(
        cycle_id="cycle-0001",
        timestamp=datetime.now(timezone.utc),
        hypothesis="Improve caching layer latency",
        status="failed",
        outcome_summary="Timed out",
        strategy="baseline",
    )
    state["refinement_ledger"] = [existing]

    payload = {
        "operation": "propose_hypothesis",
        "hypothesis": "Improve caching layer latency",
    }
    updated_state, event = process_manage_refinement_ledger_tool_response(state, _tool_message(payload))

    assert len(updated_state["refinement_ledger"]) == 1
    assert event is not None
    assert event["event"] == "refinement_ledger_rejected"
    assert "rejected" in updated_state["messages"][-1].content.lower()


def test_conclude_updates_entry_status() -> None:
    state = make_initial_context_engine_state(context_window_max=512)
    entry = RefinementLedgerEntry(
        cycle_id="cycle-0001",
        timestamp=datetime.now(timezone.utc),
        hypothesis="Ship async executor",
        status="proposed",
        outcome_summary="Pending",
    )
    state["refinement_ledger"] = [entry]

    payload = {
        "operation": "conclude_hypothesis",
        "cycle_id": "cycle-0001",
        "status": "succeeded",
        "summary": "Executor shipped",
    }
    updated_state, event = process_manage_refinement_ledger_tool_response(state, _tool_message(payload))

    updated_entry = updated_state["refinement_ledger"][0]
    assert updated_entry.status == "succeeded"
    assert "Executor shipped" in updated_entry.outcome_summary
    assert event is not None
    assert event["event"] == "refinement_ledger_concluded"


def test_infer_causal_chain_generates_insights() -> None:
    state = make_initial_context_engine_state(context_window_max=512)
    parent = RefinementLedgerEntry(
        cycle_id="cycle-0001",
        timestamp=datetime.now(timezone.utc),
        hypothesis="Add retries",
        status="failed",
        outcome_summary="Retry storm",
    )
    child = RefinementLedgerEntry(
        cycle_id="cycle-0002",
        timestamp=datetime.now(timezone.utc),
        hypothesis="Rework retries",
        status="proposed",
        outcome_summary="Investigating",
        dependencies=["cycle-0001"],
    )
    state["refinement_ledger"] = [parent, child]

    payload = {
        "operation": "infer_causal_chain",
        "cycle_ids": ["cycle-0002"],
    }
    updated_state, event = process_manage_refinement_ledger_tool_response(state, _tool_message(payload))

    updated_child = updated_state["refinement_ledger"][1]
    assert updated_child.causal_links, "Expected causal links to be recorded"
    assert event is not None and event["event"] == "refinement_ledger_causal_inference"

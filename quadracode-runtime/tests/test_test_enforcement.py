from __future__ import annotations

from datetime import datetime, timezone

from quadracode_runtime.state import (
    ExhaustionMode,
    RefinementLedgerEntry,
    make_initial_context_engine_state,
    record_property_test_result,
    record_test_suite_result,
)


def _ledger_entry() -> RefinementLedgerEntry:
    return RefinementLedgerEntry(
        cycle_id="cycle-1",
        timestamp=datetime.now(timezone.utc),
        hypothesis="ensure-tests-pass",
        status="in_progress",
        outcome_summary="pending",
    )


def test_record_test_suite_result_updates_ledger_and_state() -> None:
    state = make_initial_context_engine_state(context_window_max=512)
    state["refinement_ledger"] = [_ledger_entry()]
    payload = {
        "overall_status": "failed",
        "summary": {"commands_executed": 1, "fail_count": 1},
        "remediation": {"action": "spawn_debugger_agent", "agent_id": "debugger-xyz"},
    }

    record_test_suite_result(state, payload)

    assert state["last_test_suite_result"]["overall_status"] == "failed"
    entry = state["refinement_ledger"][-1]
    assert entry.test_results["overall_status"] == "failed"
    assert entry.exhaustion_trigger == ExhaustionMode.TEST_FAILURE
    assert state["exhaustion_mode"] == ExhaustionMode.TEST_FAILURE
    assert state["debugger_agents"][0]["agent_id"] == "debugger-xyz"


def test_record_test_suite_result_handles_passing_suite() -> None:
    state = make_initial_context_engine_state(context_window_max=512)
    state["refinement_ledger"] = [_ledger_entry()]
    payload = {
        "overall_status": "passed",
        "summary": {"commands_executed": 2, "fail_count": 0},
    }

    record_test_suite_result(state, payload)

    entry = state["refinement_ledger"][-1]
    assert entry.test_results["overall_status"] == "passed"
    assert entry.exhaustion_trigger is None or entry.exhaustion_trigger != ExhaustionMode.TEST_FAILURE


def test_record_property_test_result_failure_updates_state() -> None:
    state = make_initial_context_engine_state(context_window_max=512)
    state["refinement_ledger"] = [_ledger_entry()]
    payload = {
        "property_name": "idempotent",
        "result": {
            "status": "failed",
            "failure_message": "not idempotent",
            "failing_example": {"value": 3},
        },
    }

    record_property_test_result(state, payload)

    assert state["last_property_test_result"]["status"] == "failed"
    assert state["property_test_results"][-1]["property_name"] == "idempotent"
    assert state["exhaustion_mode"] == ExhaustionMode.TEST_FAILURE
    entry = state["refinement_ledger"][-1]
    property_tests = entry.test_results["property_tests"]
    assert property_tests[-1]["failing_example"] == {"value": 3}
    assert entry.exhaustion_trigger == ExhaustionMode.TEST_FAILURE

from __future__ import annotations

from datetime import datetime, timezone

from quadracode_runtime.state import (
    PRPState,
    RefinementLedgerEntry,
    ExhaustionMode,
    apply_prp_transition,
    make_initial_context_engine_state,
    record_skepticism_challenge,
    record_test_suite_result,
)


def _entry() -> RefinementLedgerEntry:
    return RefinementLedgerEntry(
        cycle_id="cycle-1",
        timestamp=datetime.now(timezone.utc),
        hypothesis="demo",
        status="in_progress",
        outcome_summary="…",
    )


def test_rejection_sets_test_requirement_and_clears_after_tests() -> None:
    state = make_initial_context_engine_state(context_window_max=1024)
    state["refinement_ledger"] = [_entry()]
    # Walk a valid path to PROPOSE and then rejection to HYPOTHESIZE
    apply_prp_transition(state, PRPState.EXECUTE)
    apply_prp_transition(state, PRPState.TEST)
    apply_prp_transition(state, PRPState.CONCLUDE)
    apply_prp_transition(state, PRPState.PROPOSE)
    apply_prp_transition(state, PRPState.HYPOTHESIZE, supervisor_triggered=True)

    assert state["invariants"]["needs_test_after_rejection"] is True

    # Concluding without tests logs violation but is non-fatal
    apply_prp_transition(state, PRPState.EXECUTE)
    apply_prp_transition(state, PRPState.TEST)
    apply_prp_transition(state, PRPState.CONCLUDE)
    violations = [e for e in state["prp_telemetry"] if e.get("event") == "invariant_violation"]
    assert any(v["payload"]["invariant"] == "test_after_rejection" for v in violations)

    # Recording a test clears requirement
    record_test_suite_result(state, {"overall_status": "passed"})
    assert state["invariants"]["needs_test_after_rejection"] is False


def test_context_update_required_before_conclude_or_propose() -> None:
    state = make_initial_context_engine_state(context_window_max=1024)
    state["refinement_ledger"] = [_entry()]

    # Move to CONCLUDE via a valid path, without running pre_process
    apply_prp_transition(state, PRPState.EXECUTE)
    apply_prp_transition(state, PRPState.TEST)
    apply_prp_transition(state, PRPState.CONCLUDE)
    violations = [e for e in state["prp_telemetry"] if e.get("event") == "invariant_violation"]
    assert any(v["payload"]["invariant"] == "context_update_per_cycle" for v in violations)


def test_skepticism_gate_required_before_conclude() -> None:
    state = make_initial_context_engine_state(context_window_max=2048)
    state["refinement_ledger"] = [_entry()]

    apply_prp_transition(state, PRPState.EXECUTE)
    apply_prp_transition(state, PRPState.TEST)
    apply_prp_transition(state, PRPState.CONCLUDE)

    violations = [e for e in state["prp_telemetry"] if e.get("event") == "invariant_violation"]
    assert any(v["payload"]["invariant"] == "skepticism_gate" for v in violations)


def test_skepticism_challenge_clears_invariant() -> None:
    state = make_initial_context_engine_state(context_window_max=2048)
    state["refinement_ledger"] = [_entry()]
    apply_prp_transition(state, PRPState.EXECUTE)
    record_skepticism_challenge(state, source="test", reason="unit", evidence={})
    apply_prp_transition(state, PRPState.TEST)
    apply_prp_transition(state, PRPState.CONCLUDE)

    violations = [e for e in state["prp_telemetry"] if e.get("event") == "invariant_violation"]
    assert not any(v["payload"]["invariant"] == "skepticism_gate" for v in violations)

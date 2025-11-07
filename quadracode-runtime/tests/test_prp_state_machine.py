from __future__ import annotations

from quadracode_runtime.state import (
    ExhaustionMode,
    PRPState,
    apply_prp_transition,
    make_initial_context_engine_state,
)


def test_prp_happy_path_transitions():
    state = make_initial_context_engine_state()

    apply_prp_transition(state, PRPState.EXECUTE)
    assert state["prp_state"] == PRPState.EXECUTE

    apply_prp_transition(state, PRPState.TEST)
    assert state["prp_state"] == PRPState.TEST

    state["exhaustion_mode"] = ExhaustionMode.NONE
    apply_prp_transition(state, PRPState.CONCLUDE)
    assert state["prp_state"] == PRPState.CONCLUDE

    apply_prp_transition(state, PRPState.PROPOSE)
    assert state["prp_state"] == PRPState.PROPOSE


def test_prp_requires_human_clone_to_restart_cycle():
    state = make_initial_context_engine_state()
    state["prp_state"] = PRPState.PROPOSE
    cycle_before = state.get("prp_cycle_count", 0)

    # Missing HumanClone trigger should record invalid transition
    result = apply_prp_transition(
        state,
        PRPState.HYPOTHESIZE,
        human_clone_triggered=False,
    )
    assert result == {}
    assert state["prp_state"] == PRPState.PROPOSE
    assert any(
        entry.get("event") == "prp_invalid_transition"
        for entry in state.get("metrics_log", [])
    )

    # With trigger, transition succeeds and increments cycle counter
    apply_prp_transition(state, PRPState.HYPOTHESIZE, human_clone_triggered=True)
    assert state["prp_state"] == PRPState.HYPOTHESIZE
    assert state["prp_cycle_count"] == cycle_before + 1


def test_prp_test_failure_loops_back_to_hypothesise():
    state = make_initial_context_engine_state()
    apply_prp_transition(state, PRPState.EXECUTE)
    apply_prp_transition(state, PRPState.TEST)

    state["exhaustion_mode"] = ExhaustionMode.TEST_FAILURE
    apply_prp_transition(state, PRPState.HYPOTHESIZE)
    assert state["prp_state"] == PRPState.HYPOTHESIZE

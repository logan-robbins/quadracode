from __future__ import annotations

from quadracode_runtime.state import (
    flag_false_stop_event,
    make_initial_context_engine_state,
    record_test_suite_result,
)


def test_false_stop_event_is_logged_and_pending_counter_increments() -> None:
    state = make_initial_context_engine_state(context_window_max=1024)

    payload = flag_false_stop_event(
        state,
        reason="llm_stop",
        stage="pre_process",
        evidence={"probability": 0.91},
    )

    counters = state["autonomy_counters"]
    assert counters["false_stop_events"] == 1
    assert counters["false_stop_pending"] == 1
    assert payload["reason"] == "llm_stop"

    telemetry = [entry for entry in state["prp_telemetry"] if entry.get("event") == "false_stop_detected"]
    assert telemetry, "false stop detection should emit telemetry"
    assert telemetry[-1]["payload"]["stage"] == "pre_process"


def test_successful_tests_mitigate_pending_false_stop() -> None:
    state = make_initial_context_engine_state(context_window_max=1024)
    flag_false_stop_event(state, reason="llm_stop", stage="pre_process", evidence=None)

    record_test_suite_result(state, {"overall_status": "passed", "suite": "unit"})

    counters = state["autonomy_counters"]
    assert counters["false_stop_pending"] == 0
    assert counters["false_stop_mitigated"] == 1

    telemetry = [entry for entry in state["prp_telemetry"] if entry.get("event") == "false_stop_mitigated"]
    assert telemetry, "mitigation should emit telemetry"

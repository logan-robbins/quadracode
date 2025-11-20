from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.exhaustion_predictor import ExhaustionPredictor
from quadracode_runtime.nodes.context_engine import ContextEngine
from quadracode_runtime.state import (
    ExhaustionMode,
    PRPState,
    RefinementLedgerEntry,
    make_initial_context_engine_state,
)


def _ledger_entry(
    cycle_id: int,
    *,
    exhausted: bool = True,
    status: str = "failed",
    outcome: str = "tests failed",
) -> RefinementLedgerEntry:
    return RefinementLedgerEntry(
        cycle_id=str(cycle_id),
        timestamp=datetime.now(timezone.utc) - timedelta(minutes=cycle_id),
        hypothesis=f"hypothesis-{cycle_id}",
        status=status,
        outcome_summary=outcome,
        exhaustion_trigger=(
            ExhaustionMode.TEST_FAILURE if exhausted else ExhaustionMode.NONE
        ),
        test_results={"failures": 1 if exhausted else 0},
    )


def test_exhaustion_predictor_yields_high_probability():
    ledger = [
        _ledger_entry(index, exhausted=index < 5)
        for index in range(8)
    ]
    predictor = ExhaustionPredictor(threshold=0.001)

    probability = predictor.predict_probability(ledger)

    assert 0.0 <= probability <= 1.0
    assert predictor.should_preempt(ledger) is True


def test_update_exhaustion_mode_detects_context_saturation():
    config = ContextEngineConfig()
    config.metrics_enabled = False
    config.context_window_max = 100
    config.optimal_context_size = 80
    engine = ContextEngine(config)
    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_window_used"] = 95
    state["context_window_max"] = config.context_window_max

    asyncio.run(engine._update_exhaustion_mode(state, stage="pre_process"))

    assert state["exhaustion_mode"] == ExhaustionMode.CONTEXT_SATURATION
    assert state["exhaustion_probability"] >= 0.0


def test_predicted_exhaustion_primes_prp_transition():
    config = ContextEngineConfig()
    config.metrics_enabled = False
    config.context_window_max = 256
    engine = ContextEngine(config)
    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["refinement_ledger"] = [_ledger_entry(idx) for idx in range(6)]
    state["prp_state"] = PRPState.EXECUTE

    asyncio.run(engine._update_exhaustion_mode(state, stage="pre_process"))

    assert state["exhaustion_mode"] == ExhaustionMode.PREDICTED_EXHAUSTION
    assert state["exhaustion_probability"] >= engine.exhaustion_predictor.threshold
    assert state["prp_state"] == PRPState.HYPOTHESIZE
    assert any(
        entry.get("action") == "preemptive_refinement"
        for entry in state.get("exhaustion_recovery_log", [])
    )

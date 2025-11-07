from datetime import datetime, timezone

from quadracode_runtime.long_term_memory import (
    record_episode_from_ledger,
    update_memory_guidance,
)
from quadracode_runtime.state import (
    ExhaustionMode,
    RefinementLedgerEntry,
    make_initial_context_engine_state,
)


def _ledger_entry(
    cycle_id: str,
    *,
    status: str,
    strategy: str,
    exhaustion: ExhaustionMode = ExhaustionMode.NONE,
) -> RefinementLedgerEntry:
    return RefinementLedgerEntry(
        cycle_id=cycle_id,
        timestamp=datetime.now(timezone.utc),
        hypothesis=f"Hypothesis {cycle_id}",
        status=status,
        outcome_summary=f"Outcome for {cycle_id}",
        exhaustion_trigger=exhaustion,
        strategy=strategy,
        dependencies=["cycle-1"],
        test_results={"overall_status": "passed" if status == "succeeded" else "failed"},
        novelty_score=0.42,
        predicted_success_probability=0.7,
    )


def test_record_episode_and_consolidate_memory_patterns() -> None:
    state = make_initial_context_engine_state(context_window_max=1024)
    entries = [
        _ledger_entry("cycle-2", status="succeeded", strategy="refactor"),
        _ledger_entry("cycle-3", status="failed", strategy="refactor", exhaustion=ExhaustionMode.TEST_FAILURE),
        _ledger_entry("cycle-4", status="succeeded", strategy="refactor"),
    ]

    for entry in entries:
        record_episode_from_ledger(state, entry)

    assert len(state["episodic_memory"]) == 3
    assert state["semantic_memory"], "Semantic patterns should be persisted"

    guidance = update_memory_guidance(state)
    assert guidance, "Memory guidance should be generated"
    assert guidance["recommendations"], "Guidance should include recommendations"
    assert guidance["supporting_cycles"], "Supporting cycles should anchor guidance"

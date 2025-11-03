import asyncio
from datetime import datetime, timedelta, timezone

from quadracode_runtime.config.context_engine import ContextEngineConfig
from quadracode_runtime.nodes.context_scorer import ContextScorer
from quadracode_runtime.state import make_initial_context_engine_state


def _make_segment(segment_id: str, *, priority: int, token_count: int, segment_type: str) -> dict:
    return {
        "id": segment_id,
        "content": f"{segment_type} content for {segment_id}",
        "type": segment_type,
        "priority": priority,
        "token_count": token_count,
        "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=priority * 2)).isoformat(),
        "decay_rate": 0.1,
        "compression_eligible": True,
        "restorable_reference": None,
    }


def test_context_scorer_produces_breakdown() -> None:
    config = ContextEngineConfig()
    scorer = ContextScorer(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["context_segments"] = [
        _make_segment("s1", priority=9, token_count=800, segment_type="system_prompt"),
        _make_segment("s2", priority=7, token_count=400, segment_type="recent_decisions"),
        _make_segment("s3", priority=6, token_count=600, segment_type="tool_outputs"),
    ]
    state["context_window_used"] = 1200
    state["current_phase"] = "Implementation"

    score = asyncio.run(scorer.evaluate(state))

    assert 0.0 <= score <= 1.0
    components = state["context_quality_components"]
    assert set(components.keys()) == {
        "relevance",
        "coherence",
        "completeness",
        "freshness",
        "diversity",
        "efficiency",
    }
    assert all(0.0 <= value <= 1.0 for value in components.values())


def test_score_tool_output_heuristic() -> None:
    config = ContextEngineConfig()
    scorer = ContextScorer(config)

    assert asyncio.run(scorer.score_tool_output("")) == 0.0
    short = asyncio.run(scorer.score_tool_output("short text"))
    medium = asyncio.run(scorer.score_tool_output("m" * 120))
    long = asyncio.run(scorer.score_tool_output("l" * 400))

    assert short < medium < long


def test_relevance_scores_reflect_goal_alignment() -> None:
    config = ContextEngineConfig()
    scorer = ContextScorer(config)

    state = make_initial_context_engine_state(context_window_max=config.context_window_max)
    state["task_goal"] = "Implement progressive context loader for codebase"
    state["context_segments"] = [
        {
            "id": "irrelevant",
            "content": "Document describing unrelated financial metrics",
            "type": "architecture_docs",
            "priority": 5,
            "token_count": 50,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decay_rate": 0.1,
            "compression_eligible": True,
            "restorable_reference": None,
        },
        {
            "id": "relevant",
            "content": "Discussion about progressive context loader implementation details",
            "type": "code_context",
            "priority": 8,
            "token_count": 80,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decay_rate": 0.1,
            "compression_eligible": True,
            "restorable_reference": None,
        },
    ]

    relevance = asyncio.run(scorer._score_relevance(state["context_segments"], state))  # type: ignore[attr-defined]
    assert 0.0 <= relevance <= 1.0

    # Swap priority to make irrelevant dominate and ensure score decreases
    state["context_segments"][0]["priority"] = 9
    state["context_segments"][1]["priority"] = 1
    relevance_lower = asyncio.run(scorer._score_relevance(state["context_segments"], state))  # type: ignore[attr-defined]
    assert relevance_lower < relevance

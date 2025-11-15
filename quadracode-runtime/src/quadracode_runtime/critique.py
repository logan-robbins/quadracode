"""
This module provides the core utilities for processing and translating 
hypothesis-driven critiques within the Quadracode runtime.

It is a key component of the autonomous loop, responsible for taking structured 
critiques from the `hypothesis_critique` tool and applying their insights to the 
orchestrator's state. This includes updating the refinement ledger, managing the 
critique backlog, and recording relevant metrics and error history. The module's 
primary function, `apply_hypothesis_critique`, orchestrates this entire process, 
ensuring that critiques are not just recorded, but are translated into actionable 
next steps for the autonomous system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

from quadracode_contracts import HypothesisCritiqueRecord

from .state import (
    AutonomousErrorRecord,
    QuadraCodeState,
    RefinementLedgerEntry,
)


_CATEGORY_TEMPLATES: Dict[str, Dict[str, str]] = {
    "code_quality": {
        "improvement_prefix": "Refactor code to address",
        "test_prefix": "Add regression tests ensuring",
        "metric": "lint_or_static_analysis",
    },
    "architecture": {
        "improvement_prefix": "Reshape architecture to resolve",
        "test_prefix": "Add integration tests covering",
        "metric": "architecture_consistency",
    },
    "test_coverage": {
        "improvement_prefix": "Increase coverage for",
        "test_prefix": "Add targeted tests validating",
        "metric": "coverage_ratio",
    },
    "performance": {
        "improvement_prefix": "Optimize performance for",
        "test_prefix": "Add benchmarks guarding",
        "metric": "latency_budget",
    },
}

_SEVERITY_WEIGHTS: Dict[str, float] = {
    "low": 0.25,
    "moderate": 0.5,
    "high": 0.75,
    "critical": 1.0,
}


@dataclass(slots=True)
class _Translation:
    severity_score: float
    improvements: List[Dict[str, Any]]
    tests: List[Dict[str, Any]]
    hypothesis_directive: str


def apply_hypothesis_critique(
    state: QuadraCodeState,
    record: HypothesisCritiqueRecord,
) -> Dict[str, Any]:
    """
    Translates a hypothesis critique and applies it to the orchestrator's state.

    This function serves as the main entry point for processing a critique. It 
    orchestrates the translation of the critique into actionable items and then 
    updates the relevant parts of the state, including the refinement ledger, 
    the critique backlog, and the error history.

    Args:
        state: The current state of the orchestrator.
        record: The `HypothesisCritiqueRecord` to be processed.

    Returns:
        A dictionary summarizing the translation of the critique, which is 
        used for event logging.
    """

    translation = _translate(record)
    _update_ledger(state, record, translation)
    _update_backlog(state, record, translation)
    _record_error_history(state, record, translation)
    _record_metric(state, record, translation)
    return {
        "category": _enum_value(record.category),
        "severity": _enum_value(record.severity),
        "severity_score": translation.severity_score,
        "improvements": translation.improvements,
        "tests": translation.tests,
        "hypothesis_directive": translation.hypothesis_directive,
    }


def _translate(record: HypothesisCritiqueRecord) -> _Translation:
    """
    Translates a `HypothesisCritiqueRecord` into a structured set of actions.

    This function takes a critique and converts it into a `_Translation` object, 
    which includes a list of proposed improvements, a corresponding set of 
    tests, a severity score, and a high-level directive for the next hypothesis 
    cycle.

    Args:
        record: The critique record to translate.

    Returns:
        A `_Translation` object containing the actionable items.
    """
    category = _enum_value(record.category)
    template = _CATEGORY_TEMPLATES.get(category, _CATEGORY_TEMPLATES["code_quality"])
    base_text = record.qualitative_feedback.strip() or record.critique_summary.strip()
    sentences = _explode_sentences(base_text)
    improvements: List[Dict[str, Any]] = []
    tests: List[Dict[str, Any]] = []
    for idx, sentence in enumerate(sentences, start=1):
        improvement = {
            "description": sentence,
            "directive": f"{template['improvement_prefix']} {sentence}",
            "category": category,
        }
        test_case = {
            "name": f"{category}_test_{idx}",
            "goal": f"{template['test_prefix']} {sentence}",
            "metric": template["metric"],
            "category": category,
        }
        improvements.append(improvement)
        tests.append(test_case)

    if not improvements:
        improvements.append(
            {
                "description": record.critique_summary,
                "directive": f"{template['improvement_prefix']} {record.critique_summary}",
                "category": category,
            }
        )
        tests.append(
            {
                "name": f"{category}_test_1",
                "goal": f"{template['test_prefix']} {record.critique_summary}",
                "metric": template["metric"],
                "category": category,
            }
        )

    severity_score = _severity_score(record.severity, len(improvements))
    directive = (
        f"Focus hypothesis {record.cycle_id} on {category.replace('_', ' ')} by executing "
        f"{len(tests)} targeted validations."
    )
    return _Translation(
        severity_score=severity_score,
        improvements=improvements,
        tests=tests,
        hypothesis_directive=directive,
    )


def _severity_score(severity: Any, improvement_count: int) -> float:
    """
    Calculates a numerical severity score based on the critique's severity and 
    the number of proposed improvements.
    """
    value = _enum_value(severity)
    base = _SEVERITY_WEIGHTS.get(value, 0.5)
    modifier = min(0.2, 0.05 * max(0, improvement_count - 1))
    return round(min(1.0, base + modifier), 3)


def _update_ledger(
    state: QuadraCodeState,
    record: HypothesisCritiqueRecord,
    translation: _Translation,
) -> None:
    """Updates the refinement ledger with the results of the critique."""
    entry = _find_ledger_entry(state, record.cycle_id)
    critique_payload = {
        "category": _enum_value(record.category),
        "severity": _enum_value(record.severity),
        "severity_score": translation.severity_score,
        "summary": record.critique_summary,
        "feedback": record.qualitative_feedback,
        "improvements": translation.improvements,
        "tests": translation.tests,
        "evidence": list(record.evidence),
        "timestamp": record.recorded_at,
    }
    if entry is None:
        return

    log = entry.metadata.setdefault("critiques", [])
    if isinstance(log, list):
        log.append(critique_payload)
    entry.metadata["pending_tests"] = translation.tests
    entry.metadata["priority_score"] = max(
        [crit.get("severity_score", 0.0) for crit in log] + [translation.severity_score],
    )


def _update_backlog(
    state: QuadraCodeState,
    record: HypothesisCritiqueRecord,
    translation: _Translation,
) -> None:
    """Adds the critique to the prioritized critique backlog."""
    backlog = state.setdefault("critique_backlog", [])
    if not isinstance(backlog, list):
        backlog = []
        state["critique_backlog"] = backlog
    backlog.append(
        {
            "cycle_id": record.cycle_id,
            "hypothesis": record.hypothesis,
            "category": _enum_value(record.category),
            "severity": _enum_value(record.severity),
            "severity_score": translation.severity_score,
            "tests": translation.tests,
            "improvements": translation.improvements,
            "directive": translation.hypothesis_directive,
            "recorded_at": record.recorded_at,
        }
    )
    backlog.sort(key=lambda item: item.get("severity_score", 0), reverse=True)
    state["critique_backlog"] = backlog[:25]


def _record_error_history(
    state: QuadraCodeState,
    record: HypothesisCritiqueRecord,
    translation: _Translation,
) -> None:
    """Records the critique as a non-fatal error in the error history."""
    errors = state.setdefault("error_history", [])
    entry: AutonomousErrorRecord = {
        "error_type": f"critique::{_enum_value(record.category)}",
        "description": record.critique_summary,
        "recovery_attempts": [item["directive"] for item in translation.improvements],
        "escalated": False,
        "resolved": False,
        "timestamp": record.recorded_at,
    }
    errors.append(entry)


def _record_metric(
    state: QuadraCodeState,
    record: HypothesisCritiqueRecord,
    translation: _Translation,
) -> None:
    """Records a metric for the critique event."""
    metrics = state.setdefault("metrics_log", [])
    metrics.append(
        {
            "event": "hypothesis_critique_recorded",
            "payload": {
                "cycle_id": record.cycle_id,
                "category": _enum_value(record.category),
                "severity": _enum_value(record.severity),
                "severity_score": translation.severity_score,
                "test_count": len(translation.tests),
            },
        }
    )


def _find_ledger_entry(
    state: QuadraCodeState,
    cycle_id: str,
) -> RefinementLedgerEntry | None:
    """Finds a specific entry in the refinement ledger by its cycle ID."""
    ledger = state.get("refinement_ledger")
    if not isinstance(ledger, list):
        return None
    for entry in ledger:
        ref = entry
        if isinstance(ref, RefinementLedgerEntry) and ref.cycle_id == cycle_id:
            return ref
        if isinstance(ref, dict) and ref.get("cycle_id") == cycle_id:
            try:
                hydrated = RefinementLedgerEntry(**ref)
            except Exception:
                continue
            return hydrated
    return None


def _explode_sentences(text: str) -> List[str]:
    """Splits a block of text into a list of sentences."""
    chunks = re.split(r"[\n\.;!?]+", text)
    sentences = [chunk.strip() for chunk in chunks if chunk.strip()]
    return sentences[:5]


def _enum_value(value: Any) -> str:
    """Safely extracts the string value from an enum member."""
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)

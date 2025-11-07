"""Hypothesis-driven critique processing and translation utilities."""

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
    """Translate a critique and apply it to orchestrator state."""

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
    value = _enum_value(severity)
    base = _SEVERITY_WEIGHTS.get(value, 0.5)
    modifier = min(0.2, 0.05 * max(0, improvement_count - 1))
    return round(min(1.0, base + modifier), 3)


def _update_ledger(
    state: QuadraCodeState,
    record: HypothesisCritiqueRecord,
    translation: _Translation,
) -> None:
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
    chunks = re.split(r"[\n\.;!?]+", text)
    sentences = [chunk.strip() for chunk in chunks if chunk.strip()]
    return sentences[:5]


def _enum_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)

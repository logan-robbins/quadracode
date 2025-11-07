from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, MutableMapping


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_violation(state: MutableMapping[str, Any], code: str, details: Dict[str, Any]) -> None:
    bucket = state.setdefault("invariants", {})
    if not isinstance(bucket, dict):
        bucket = {}
        state["invariants"] = bucket
    log = bucket.setdefault("violation_log", [])
    if isinstance(log, list):
        log.append({
            "timestamp": _now_iso(),
            "code": code,
            "details": details,
        })


def mark_context_updated(state: MutableMapping[str, Any]) -> None:
    bucket = state.setdefault("invariants", {})
    if isinstance(bucket, dict):
        bucket["context_updated_in_cycle"] = True


def mark_rejection_requires_tests(state: MutableMapping[str, Any]) -> None:
    bucket = state.setdefault("invariants", {})
    if isinstance(bucket, dict):
        bucket["needs_test_after_rejection"] = True
        bucket["context_updated_in_cycle"] = False


def clear_test_requirement(state: MutableMapping[str, Any]) -> None:
    bucket = state.setdefault("invariants", {})
    if isinstance(bucket, dict):
        bucket["needs_test_after_rejection"] = False


def check_transition_invariants(
    state: MutableMapping[str, Any],
    *,
    from_state: str,
    to_state: str,
) -> List[Dict[str, Any]]:
    """Evaluate invariants on PRP transitions; return any violations.

    Invariants enforced:
    - test_after_rejection: After PROPOSE→HYPOTHESIZE (HumanClone rejection), a test must run before CONCLUDE/PROPOSE.
    - context_update_per_cycle: Every cycle should include a context update before CONCLUDE/PROPOSE.
    """

    violations: List[Dict[str, Any]] = []
    bucket = state.get("invariants", {}) if isinstance(state.get("invariants"), dict) else {}
    needs_test = bool(bucket.get("needs_test_after_rejection", False))
    context_updated = bool(bucket.get("context_updated_in_cycle", False))
    skepticism_satisfied = bool(bucket.get("skepticism_gate_satisfied", False))

    if to_state in {"conclude", "propose"}:
        if needs_test:
            payload = {
                "invariant": "test_after_rejection",
                "from": from_state,
                "to": to_state,
            }
            _log_violation(state, "test_after_rejection", payload)
            violations.append(payload)
        if not context_updated:
            payload = {
                "invariant": "context_update_per_cycle",
                "from": from_state,
                "to": to_state,
            }
            _log_violation(state, "context_update_per_cycle", payload)
            violations.append(payload)
        if not skepticism_satisfied:
            payload = {
                "invariant": "skepticism_gate",
                "from": from_state,
                "to": to_state,
            }
            _log_violation(state, "skepticism_gate", payload)
            violations.append(payload)

    return violations

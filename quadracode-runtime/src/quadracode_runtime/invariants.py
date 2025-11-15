"""
This module is responsible for defining and enforcing the invariants of the 
Plan-Refine-Play (PRP) state machine.

Invariants are a set of rules that must hold true at specific points in the 
PRP cycle. They are a critical mechanism for ensuring the robustness and 
predictability of the autonomous loop. This module provides a set of functions for 
marking the state with the necessary flags to track these invariants, as well as 
a `check_transition_invariants` function that is called during state transitions 
to verify that the invariants have been met. Any violations are logged, providing 
a clear audit trail for debugging and analysis.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, MutableMapping


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_violation(state: MutableMapping[str, Any], code: str, details: Dict[str, Any]) -> None:
    """Logs an invariant violation to the state."""
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
    """
    Marks the 'context_updated_in_cycle' invariant as satisfied for the 
    current cycle.
    """
    bucket = state.setdefault("invariants", {})
    if isinstance(bucket, dict):
        bucket["context_updated_in_cycle"] = True


def mark_rejection_requires_tests(state: MutableMapping[str, Any]) -> None:
    """
    Sets the 'needs_test_after_rejection' invariant, indicating that a test 
    must be run before the next conclusion or proposal.
    """
    bucket = state.setdefault("invariants", {})
    if isinstance(bucket, dict):
        bucket["needs_test_after_rejection"] = True
        bucket["context_updated_in_cycle"] = False


def clear_test_requirement(state: MutableMapping[str, Any]) -> None:
    """
    Clears the 'needs_test_after_rejection' invariant, typically after a test 
    has been successfully run.
    """
    bucket = state.setdefault("invariants", {})
    if isinstance(bucket, dict):
        bucket["needs_test_after_rejection"] = False


def check_transition_invariants(
    state: MutableMapping[str, Any],
    *,
    from_state: str,
    to_state: str,
) -> List[Dict[str, Any]]:
    """
    Evaluates the state against a set of predefined invariants before a PRP 
    state transition.

    This function is the core of the invariant enforcement system. It checks for 
    violations of key invariants, such as ensuring that a test is run after a 
    rejection, and logs any violations that are found.

    Args:
        state: The current state of the system.
        from_state: The source state of the transition.
        to_state: The target state of the transition.

    Returns:
        A list of any invariant violations that were detected.
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

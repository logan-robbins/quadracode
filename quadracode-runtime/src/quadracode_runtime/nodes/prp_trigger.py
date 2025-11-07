"""Graph node that converts HumanClone responses into PRP triggers."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from quadracode_contracts import HUMAN_CLONE_RECIPIENT, HumanCloneTrigger

from ..autonomous import process_autonomous_tool_response
from ..state import (
    ExhaustionMode,
    RefinementLedgerEntry,
    PRPState,
    QuadraCodeState,
    apply_prp_transition,
)
from ..prp import parse_human_clone_trigger
from ..workspace_integrity import capture_workspace_snapshot


def _ensure_state_defaults(state: QuadraCodeState) -> None:
    state.setdefault("human_clone_requirements", [])
    state.setdefault("human_clone_trigger", {})


def _render_summary(trigger: HumanCloneTrigger) -> str:
    lines = [
        "HumanClone Trigger Received:",
        f"- Cycle iteration: {trigger.cycle_iteration}",
        f"- Exhaustion mode: {trigger.exhaustion_mode.value}",
    ]
    if trigger.required_artifacts:
        lines.append("- Required artifacts:")
        for artifact in trigger.required_artifacts:
            lines.append(f"  * {artifact}")
    if trigger.rationale:
        lines.append(f"- Rationale: {trigger.rationale}")
    return "\n".join(lines)


def _make_tool_message(state: QuadraCodeState, trigger: HumanCloneTrigger) -> ToolMessage:
    cycle_id, hypothesis = _resolve_hypothesis_context(state, trigger)
    category = _infer_category(trigger)
    severity = _infer_severity(trigger)
    feedback_lines = [
        f"Rejection driven by exhaustion mode {trigger.exhaustion_mode.value}.",
    ]
    if trigger.rationale:
        feedback_lines.append(trigger.rationale)
    if trigger.required_artifacts:
        feedback_lines.append(
            "Artifacts requested: " + ", ".join(trigger.required_artifacts)
        )
    qualitative_feedback = " ".join(feedback_lines)
    record = {
        "event": "hypothesis_critique",
        "record": {
            "cycle_id": cycle_id,
            "hypothesis": hypothesis,
            "critique_summary": f"Cycle {cycle_id} rejected by HumanClone",
            "qualitative_feedback": qualitative_feedback,
            "category": category,
            "severity": severity,
            "evidence": list(trigger.required_artifacts),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }
    tool_call_id = f"human_clone_trigger::{trigger.cycle_iteration}"
    return ToolMessage(
        content=json.dumps(record, separators=(",", ":")),
        name="hypothesis_critique",
        tool_call_id=tool_call_id,
        additional_kwargs={
            "source": "human_clone",
            "trigger": trigger.model_dump(),
        },
    )


def _apply_trigger_to_state(
    state: QuadraCodeState,
    trigger: HumanCloneTrigger,
) -> None:
    _ensure_state_defaults(state)
    state["human_clone_trigger"] = trigger.model_dump()
    state["human_clone_requirements"] = list(trigger.required_artifacts)
    state["exhaustion_mode"] = ExhaustionMode(trigger.exhaustion_mode.value)

    apply_prp_transition(
        state,
        PRPState.HYPOTHESIZE,
        exhaustion_mode=state["exhaustion_mode"],
        human_clone_triggered=True,
    )


def prp_trigger_check(state: QuadraCodeState) -> QuadraCodeState:
    """Intercept HumanClone responses and convert them into PRP triggers."""

    last_sender = state.pop("_last_envelope_sender", None)
    if last_sender != HUMAN_CLONE_RECIPIENT:
        return state

    messages = list(state.get("messages") or [])
    if not messages:
        return state

    trailing = messages[-1]
    if not isinstance(trailing, HumanMessage):
        return state

    content = trailing.content
    if not isinstance(content, str):
        content = str(content)

    trigger = parse_human_clone_trigger(content)
    _apply_trigger_to_state(state, trigger)

    summary = SystemMessage(
        content=_render_summary(trigger),
        additional_kwargs={"source": "human_clone_trigger"},
    )
    tool_message = _make_tool_message(state, trigger)

    messages.pop()
    messages.extend([summary, tool_message])
    state["messages"] = messages

    updated_state, event_record = process_autonomous_tool_response(state, tool_message)
    if event_record:
        metrics = updated_state.setdefault("metrics_log", [])
        metrics.append(
            {
                "event": "human_clone_trigger",
                "payload": {
                    "trigger": trigger.model_dump(),
                    "autonomous_event": event_record,
                },
            }
        )

    exhaustion_value = updated_state.get("exhaustion_mode")
    exhaustion_mode: ExhaustionMode | None
    if isinstance(exhaustion_value, ExhaustionMode):
        exhaustion_mode = exhaustion_value
    elif isinstance(exhaustion_value, str):
        try:
            exhaustion_mode = ExhaustionMode(exhaustion_value)
        except ValueError:
            exhaustion_mode = None
    else:
        exhaustion_mode = None

    capture_workspace_snapshot(
        updated_state,
        reason="human_clone_rejection",
        stage="human_clone_review",
        exhaustion_mode=exhaustion_mode,
        metadata={
            "cycle_iteration": trigger.cycle_iteration,
            "required_artifacts": list(trigger.required_artifacts),
        },
    )

    return updated_state


def _resolve_hypothesis_context(
    state: QuadraCodeState,
    trigger: HumanCloneTrigger,
) -> tuple[str, str]:
    ledger = state.get("refinement_ledger")
    if isinstance(ledger, list) and ledger:
        entry = ledger[-1]
        if isinstance(entry, RefinementLedgerEntry):
            return entry.cycle_id, entry.hypothesis
        if isinstance(entry, dict):
            cycle_id = str(entry.get("cycle_id") or f"cycle-{trigger.cycle_iteration}")
            hypothesis = str(entry.get("hypothesis") or f"Refinement cycle {trigger.cycle_iteration}")
            return cycle_id, hypothesis
    fallback_cycle = f"cycle-{trigger.cycle_iteration}"
    return fallback_cycle, f"Refinement cycle {trigger.cycle_iteration}"


def _infer_category(trigger: HumanCloneTrigger) -> str:
    haystack = " ".join(list(trigger.required_artifacts) + [trigger.rationale or ""]).lower()
    if "test" in haystack or "coverage" in haystack:
        return "test_coverage"
    if "perf" in haystack or "latency" in haystack or "throughput" in haystack:
        return "performance"
    if "arch" in haystack or "design" in haystack or "topology" in haystack:
        return "architecture"
    return "code_quality"


def _infer_severity(trigger: HumanCloneTrigger) -> str:
    mapping = {
        ExhaustionMode.HYPOTHESIS_EXHAUSTED: "critical",
        ExhaustionMode.TEST_FAILURE: "high",
        ExhaustionMode.RETRY_DEPLETION: "high",
        ExhaustionMode.TOOL_BACKPRESSURE: "moderate",
        ExhaustionMode.CONTEXT_SATURATION: "moderate",
        ExhaustionMode.LLM_STOP: "moderate",
    }
    severity = mapping.get(trigger.exhaustion_mode, "low")
    if len(trigger.required_artifacts) >= 3 and severity != "critical":
        return "high"
    return severity

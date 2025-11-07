from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, MutableMapping, Tuple

from langchain_core.messages import ToolMessage

from quadracode_contracts import (
    AutonomousCheckpointRecord,
    AutonomousEscalationRecord,
    AutonomousRoutingDirective,
    HUMAN_CLONE_RECIPIENT,
    HypothesisCritiqueRecord,
)

from .critique import apply_hypothesis_critique
from .observability import get_meta_observer
from .time_travel import get_time_travel_recorder
from .state import (
    AutonomousErrorRecord,
    AutonomousMilestone,
    ContextEngineState,
    ExhaustionMode,
    record_test_suite_result,
)


AUTONOMOUS_TOOL_NAMES = {
    "autonomous_checkpoint",
    "request_final_review",
    "escalate_to_human",
    "hypothesis_critique",
    # Accept built-in escalate tool without special aliasing
    "autonomous_escalate",
}


def _attach_common_event_metadata(
    record: Dict[str, Any],
    *,
    tool_response: ToolMessage | None = None,
    thread_id: Any = None,
    categories: List[str] | None = None,
) -> None:
    payload = record.setdefault("payload", {})
    if not isinstance(payload, dict):
        return
    if thread_id and "thread_id" not in payload:
        payload["thread_id"] = str(thread_id)
    if tool_response and tool_response.tool_call_id and "tool_call_id" not in payload:
        payload["tool_call_id"] = tool_response.tool_call_id
    if categories:
        existing = payload.get("categories")
        if isinstance(existing, list):
            payload["categories"] = sorted(set(existing + categories))
        else:
            payload["categories"] = categories


_AUTONOMY_OBSERVER = get_meta_observer()
_TIME_TRAVEL = get_time_travel_recorder()


def _publish_autonomous_event(event_record: Dict[str, Any] | None, state: ContextEngineState) -> None:
    if not event_record:
        return
    payload = event_record.get("payload", {})
    if isinstance(payload, dict):
        payload.setdefault("loop_depth", int(state.get("prp_cycle_count", 0) or 0))
    try:
        _AUTONOMY_OBSERVER.publish_autonomous_event(event_record["event"], payload)  # type: ignore[index]
    except Exception:  # pragma: no cover - observability is best-effort
        pass
    try:
        _TIME_TRAVEL.log_transition(
            state,
            event=f"autonomous.{event_record.get('event', 'unknown')}",
            payload=payload if isinstance(payload, dict) else {},
        )
    except Exception:  # pragma: no cover - best-effort
        pass

def process_autonomous_tool_response(
    state: ContextEngineState,
    tool_response: Any,
) -> Tuple[ContextEngineState, Dict[str, Any] | None]:
    """Update state from HUMAN_OBSOLETE autonomous tool invocations."""

    if not isinstance(tool_response, ToolMessage):
        return state, None

    tool_name = (tool_response.name or "").strip()
    if tool_name not in AUTONOMOUS_TOOL_NAMES:
        return state, None

    payload = _parse_tool_message(tool_response)
    if not isinstance(payload, dict):
        return state, None

    event = payload.get("event")
    event_record: Dict[str, Any] | None = None

    thread_id = state.get("thread_id")
    exhaustion_mode = state.get("exhaustion_mode", ExhaustionMode.NONE)
    if isinstance(exhaustion_mode, str):
        try:
            exhaustion_mode = ExhaustionMode(exhaustion_mode)
        except ValueError:
            exhaustion_mode = ExhaustionMode.NONE

    if event == "checkpoint":
        record_payload = payload.get("record")
        if not isinstance(record_payload, dict):
            return state, None
        try:
            record = AutonomousCheckpointRecord(**record_payload)
        except Exception:
            return state, None
        milestone_entry: AutonomousMilestone = {
            "milestone": record.milestone,
            "title": record.title,
            "status": record.status,
            "summary": record.summary,
            "next_steps": list(record.next_steps),
            "updated_at": record.recorded_at,
        }
        milestones = state.setdefault("milestones", [])
        _upsert_milestone(milestones, milestone_entry)
        title = record.title or f"Milestone {record.milestone}"
        state["current_phase"] = title
        event_record = {
            "event": "checkpoint",
            "payload": {
                "record": record.model_dump(mode="json"),
                "exhaustion_mode": exhaustion_mode.value,
            },
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["checkpoint"],
        )
        _publish_autonomous_event(event_record, state)
        return state, event_record

    if event == "final_review_request":
        record_payload = payload.get("record")
        if not isinstance(record_payload, dict):
            return state, None
        try:
            record = AutonomousEscalationRecord(**record_payload)
        except Exception:
            return state, None

        state["autonomous_routing"] = AutonomousRoutingDirective(
            deliver_to_human=False,
            escalate=False,
            recipient=HUMAN_CLONE_RECIPIENT,
        ).to_payload()
        state["current_phase"] = "awaiting_review"
        tests_payload = payload.get("tests")
        if isinstance(tests_payload, dict):
            record_test_suite_result(state, tests_payload)
        event_record = {
            "event": "final_review_request",
            "payload": {
                "record": record.model_dump(mode="json"),
                "exhaustion_mode": exhaustion_mode.value,
            },
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["review_request"],
        )
        _publish_autonomous_event(event_record, state)
        return state, event_record

    if event == "escalation":
        record_payload = payload.get("record")
        routing_payload = payload.get("routing")
        if not isinstance(record_payload, dict) or not isinstance(routing_payload, dict):
            return state, None
        try:
            record = AutonomousEscalationRecord(**record_payload)
            routing = AutonomousRoutingDirective(**routing_payload)
        except Exception:
            return state, None

        errors = state.setdefault("error_history", [])
        escalation_entry: AutonomousErrorRecord = {
            "error_type": record.error_type,
            "description": record.description,
            "recovery_attempts": list(record.recovery_attempts),
            "escalated": True,
            "resolved": False,
            "timestamp": record.timestamp,
        }
        errors.append(escalation_entry)
        state["autonomous_routing"] = routing.to_payload()
        state["current_phase"] = "awaiting_human"
        event_record = {
            "event": "escalation",
            "payload": {
                "record": record.model_dump(mode="json"),
                "routing": routing.to_payload(),
                "exhaustion_mode": exhaustion_mode.value,
            },
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["escalation"],
        )
        _publish_autonomous_event(event_record, state)
        return state, event_record

    if event == "hypothesis_critique":
        record_payload = payload.get("record")
        if not isinstance(record_payload, dict):
            return state, None
        try:
            record = HypothesisCritiqueRecord(**record_payload)
        except Exception:
            return state, None

        translation = apply_hypothesis_critique(state, record)
        event_record = {
            "event": "hypothesis_critique",
            "payload": {
                "record": record.model_dump(mode="json"),
                "translation": translation,
                "exhaustion_mode": exhaustion_mode.value,
            },
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["critique", "hypothesis"],
        )
        _publish_autonomous_event(event_record, state)
        return state, event_record

    return state, None


def _parse_tool_message(message: ToolMessage) -> Dict[str, Any] | None:
    content = message.content
    text: str
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, MutableMapping):
                value = item.get("text")
                if isinstance(value, str):
                    parts.append(value)
            else:
                parts.append(str(item))
        text = "".join(parts)
    else:
        text = str(content)

    text = text.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _upsert_milestone(milestones: List[AutonomousMilestone], entry: AutonomousMilestone) -> None:
    updated = False
    for idx, existing in enumerate(milestones):
        if existing.get("milestone") == entry.get("milestone"):
            merged = {**existing, **entry}
            milestones[idx] = merged
            updated = True
            break

    if not updated:
        milestones.append(entry)
        milestones.sort(key=lambda item: item.get("milestone", 0))

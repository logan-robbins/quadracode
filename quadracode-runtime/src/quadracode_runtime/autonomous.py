from __future__ import annotations

import json
from typing import Any, Dict, List, MutableMapping, Tuple

from langchain_core.messages import ToolMessage

from quadracode_contracts import (
    AutonomousCheckpointRecord,
    AutonomousCritiqueRecord,
    AutonomousEscalationRecord,
    AutonomousRoutingDirective,
    HUMAN_CLONE_RECIPIENT,
)

from .state import AutonomousErrorRecord, AutonomousMilestone, ContextEngineState


AUTONOMOUS_TOOL_NAMES = {
    "autonomous_checkpoint",
    "request_final_review",
    "escalate_to_human",
    "autonomous_critique",
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
            "payload": {"record": record.dict()},
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["checkpoint"],
        )
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
        event_record = {
            "event": "final_review_request",
            "payload": {"record": record.dict()},
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["review_request"],
        )
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
                "record": record.dict(),
                "routing": routing.to_payload(),
            },
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["escalation"],
        )
        return state, event_record

    if event == "critique":
        record_payload = payload.get("record")
        if not isinstance(record_payload, dict):
            return state, None
        try:
            record = AutonomousCritiqueRecord(**record_payload)
        except Exception:
            return state, None
        errors = state.setdefault("error_history", [])
        critique_entry: AutonomousErrorRecord = {
            "error_type": "critique",
            "description": f"{record.action_taken}: {record.outcome}",
            "recovery_attempts": list(record.improvements),
            "escalated": False,
            "resolved": False,
            "timestamp": record.recorded_at,
        }
        errors.append(critique_entry)
        event_record = {
            "event": "critique",
            "payload": {"record": record.dict()},
        }
        _attach_common_event_metadata(
            event_record,
            tool_response=tool_response,
            thread_id=thread_id,
            categories=["critique"],
        )
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

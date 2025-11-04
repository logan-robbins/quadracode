from __future__ import annotations

import json
import os
import shlex
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any, Dict, List, Optional

import httpx
import redis
import streamlit as st
from streamlit.runtime.scriptrunner_utils.script_run_context import (
    add_script_run_ctx,
    get_script_run_ctx,
)
from streamlit.runtime.scriptrunner_utils.script_requests import RerunData

from quadracode_contracts import (
    HUMAN_CLONE_RECIPIENT,
    HUMAN_RECIPIENT,
    MAILBOX_PREFIX,
    ORCHESTRATOR_RECIPIENT,
    MessageEnvelope,
)
from quadracode_contracts.messaging import mailbox_key
from quadracode_tools.tools.workspace import (
    workspace_copy_from,
    workspace_create,
    workspace_destroy,
    workspace_exec,
)


def _int_env(var_name: str, default: int) -> int:
    value = os.environ.get(var_name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
AGENT_REGISTRY_URL = os.environ.get("AGENT_REGISTRY_URL", "")
UI_BARE = os.environ.get("UI_BARE", "0") == "1"
CONTEXT_METRICS_STREAM = os.environ.get("CONTEXT_METRICS_STREAM", "qc:context:metrics")
CONTEXT_METRICS_LIMIT = int(os.environ.get("CONTEXT_METRICS_LIMIT", "200"))
AUTONOMOUS_EVENTS_STREAM = os.environ.get("AUTONOMOUS_EVENTS_STREAM", "qc:autonomous:events")
AUTONOMOUS_EVENTS_LIMIT = int(os.environ.get("AUTONOMOUS_EVENTS_LIMIT", "200"))
WORKSPACE_EXPORT_ROOT = Path(os.environ.get("QUADRACODE_WORKSPACE_EXPORT_ROOT", "./workspace_exports")).expanduser()
WORKSPACE_LOG_TAIL_LINES = _int_env("QUADRACODE_WORKSPACE_LOG_TAIL_LINES", 400)
WORKSPACE_LOG_LIST_LIMIT = _int_env("QUADRACODE_WORKSPACE_LOG_LIST_LIMIT", 20)
WORKSPACE_STREAM_PREFIX = os.environ.get("QUADRACODE_WORKSPACE_STREAM_PREFIX", "qc:workspace")
WORKSPACE_EVENTS_LIMIT = _int_env("QUADRACODE_WORKSPACE_EVENTS_LIMIT", 50)

MAILBOX_ORCHESTRATOR = mailbox_key(ORCHESTRATOR_RECIPIENT)
MAILBOX_HUMAN = mailbox_key(HUMAN_RECIPIENT)


@st.cache_resource(show_spinner=False)
def get_redis_client() -> redis.Redis:
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def _active_supervisor() -> str:
    value = st.session_state.get("supervisor_recipient")
    if value in {HUMAN_RECIPIENT, HUMAN_CLONE_RECIPIENT}:
        return value
    return HUMAN_RECIPIENT


def _set_supervisor(recipient: str, chat_id: str | None = None) -> None:
    target = recipient if recipient in {HUMAN_RECIPIENT, HUMAN_CLONE_RECIPIENT} else HUMAN_RECIPIENT
    st.session_state.supervisor_recipient = target
    supervisors = st.session_state.get("chat_supervisors")
    if not isinstance(supervisors, dict):
        supervisors = {}
        st.session_state.chat_supervisors = supervisors
    if chat_id:
        supervisors[chat_id] = target


def _supervisor_mailbox() -> str:
    return mailbox_key(_active_supervisor())


def _get_last_seen(chat_id: str, supervisor: str) -> str | None:
    last_seen_map = st.session_state.chat_last_seen  # type: ignore[attr-defined]
    entry = last_seen_map.get(chat_id) if isinstance(last_seen_map, dict) else None
    if isinstance(entry, dict):
        value = entry.get(supervisor)
        if isinstance(value, str):
            return value
        return None
    if isinstance(entry, str) and supervisor == HUMAN_RECIPIENT:
        return entry
    return None


def _set_last_seen(chat_id: str, supervisor: str, entry_id: str) -> None:
    last_seen_map = st.session_state.chat_last_seen  # type: ignore[attr-defined]
    if not isinstance(last_seen_map, dict):
        last_seen_map = {}
        st.session_state.chat_last_seen = last_seen_map  # type: ignore[attr-defined]
    existing = last_seen_map.get(chat_id)
    if isinstance(existing, dict):
        existing[supervisor] = entry_id
    elif isinstance(existing, str):
        last_seen_map[chat_id] = {HUMAN_RECIPIENT: existing, supervisor: entry_id}
    else:
        last_seen_map[chat_id] = {supervisor: entry_id}


def _baseline_last_seen(client: redis.Redis, supervisor: str) -> str:
    mailbox = mailbox_key(supervisor)
    latest = client.xrevrange(mailbox, count=1)
    return latest[0][0] if latest else "0-0"


def _reset_last_seen_for_active_chat(client: redis.Redis) -> None:
    chat_id = st.session_state.chat_id
    if not chat_id:
        return
    supervisor = _active_supervisor()
    baseline = _baseline_last_seen(client, supervisor)
    _set_last_seen(chat_id, supervisor, baseline)
    st.session_state.last_seen_id = baseline


def _ensure_session_defaults() -> None:
    st.session_state.setdefault("chats", [])
    st.session_state.setdefault("chat_histories", {})
    st.session_state.setdefault("chat_last_seen", {})
    st.session_state.setdefault("chat_id", None)
    st.session_state.setdefault("history", [])  # type: ignore[attr-defined]
    st.session_state.setdefault("last_seen_id", "0-0")
    st.session_state.setdefault("chat_selector", None)
    st.session_state.setdefault("supervisor_recipient", HUMAN_RECIPIENT)
    st.session_state.setdefault("chat_supervisors", {})
    st.session_state.setdefault("autonomous_mode_enabled", False)
    st.session_state.setdefault("autonomous_max_iterations", 1000)
    st.session_state.setdefault("autonomous_max_hours", 48.0)
    st.session_state.setdefault("autonomous_max_agents", 4)
    st.session_state.setdefault("autonomous_chat_settings", {})
    st.session_state.setdefault("autonomous_mode_toggle", False)
    st.session_state.setdefault("autonomous_max_iterations_input", 1000)
    st.session_state.setdefault("autonomous_max_hours_input", 48.0)
    st.session_state.setdefault("autonomous_max_agents_input", 4)
    st.session_state.setdefault("workspace_descriptors", {})
    st.session_state.setdefault("workspace_messages", [])
    st.session_state.setdefault("workspace_log_selection", {})


def _get_chat_entry(chat_id: str) -> Optional[Dict[str, Any]]:
    for chat in st.session_state.chats:  # type: ignore[attr-defined]
        if chat["id"] == chat_id:
            return chat
    return None


def _promote_chat(chat_id: str) -> None:
    chats = st.session_state.chats  # type: ignore[attr-defined]
    entry = None
    remainder = []
    for chat in chats:
        if chat["id"] == chat_id:
            entry = chat
        else:
            remainder.append(chat)
    if entry is not None:
        st.session_state.chats = [entry, *remainder]  # type: ignore[attr-defined]

def _set_active_chat(chat_id: str, *, client: Optional[redis.Redis] = None) -> None:
    entry = _get_chat_entry(chat_id)
    if entry is None:
        return
    histories = st.session_state.chat_histories  # type: ignore[attr-defined]
    history = histories.setdefault(chat_id, [])
    supervisors = st.session_state.chat_supervisors  # type: ignore[attr-defined]
    if not isinstance(supervisors, dict):
        supervisors = {}
        st.session_state.chat_supervisors = supervisors  # type: ignore[attr-defined]

    settings_map_candidate = st.session_state.autonomous_chat_settings  # type: ignore[attr-defined]
    if isinstance(settings_map_candidate, dict):
        chat_settings = settings_map_candidate.get(chat_id, {})
    else:
        chat_settings = {}

    supervisor = supervisors.get(chat_id)
    if supervisor not in {HUMAN_RECIPIENT, HUMAN_CLONE_RECIPIENT}:
        supervisor = HUMAN_CLONE_RECIPIENT if chat_settings else HUMAN_RECIPIENT
    _set_supervisor(supervisor, chat_id)

    last_seen = _get_last_seen(chat_id, supervisor)
    if last_seen is None and client is not None:
        last_seen = _baseline_last_seen(client, supervisor)
        _set_last_seen(chat_id, supervisor, last_seen)
    st.session_state.chat_id = chat_id
    st.session_state.history = history  # type: ignore[attr-defined]
    st.session_state.last_seen_id = last_seen or "0-0"
    st.session_state.chat_selector = chat_id
    _promote_chat(chat_id)

    settings_map = st.session_state.autonomous_chat_settings  # type: ignore[attr-defined]
    if isinstance(settings_map, dict):
        chat_settings = settings_map.get(chat_id, {})
    else:
        chat_settings = {}
    if chat_settings:
        st.session_state.autonomous_mode_enabled = True
        st.session_state.autonomous_max_iterations = int(chat_settings.get("max_iterations", 1000))
        st.session_state.autonomous_max_hours = float(chat_settings.get("max_hours", 48.0))
        st.session_state.autonomous_max_agents = int(chat_settings.get("max_agents", 4))
        st.session_state.autonomous_mode_toggle = True
        st.session_state.autonomous_max_iterations_input = st.session_state.autonomous_max_iterations
        st.session_state.autonomous_max_hours_input = st.session_state.autonomous_max_hours
        st.session_state.autonomous_max_agents_input = st.session_state.autonomous_max_agents
    else:
        st.session_state.autonomous_mode_enabled = False
        st.session_state.autonomous_mode_toggle = False


def _create_chat(client: redis.Redis, *, title: Optional[str] = None) -> str:
    chat_id = uuid.uuid4().hex
    entry = {
        "id": chat_id,
        "title": title or "New chat",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    st.session_state.chats = [entry, *st.session_state.chats]  # type: ignore[attr-defined]
    st.session_state.chat_histories[chat_id] = []  # type: ignore[attr-defined]
    _set_supervisor(HUMAN_RECIPIENT, chat_id)
    baseline = _baseline_last_seen(client, HUMAN_RECIPIENT)
    _set_last_seen(chat_id, HUMAN_RECIPIENT, baseline)
    _set_active_chat(chat_id, client=client)
    return chat_id


def _append_history(
    role: str,
    content: str,
    *,
    ticket_id: str | None = None,
    trace: List[Dict[str, Any]] | None = None,
    sender: str | None = None,
) -> None:
    entry: Dict[str, Any] = {"role": role, "content": content}
    if ticket_id:
        entry["ticket_id"] = ticket_id
    if trace:
        entry["trace"] = trace
    if sender:
        entry["sender"] = sender
    st.session_state.history.append(entry)
    chat_id = st.session_state.chat_id
    if chat_id:
        if role in {"human", "user"}:
            summary = (content or "").strip()
            if summary:
                chat_entry = _get_chat_entry(chat_id)
                if chat_entry is not None:
                    current_title = chat_entry.get("title", "")
                    if current_title in {"New chat", "Untitled chat"} or current_title.startswith("Chat "):
                        snippet = summary[:60]
                        if len(summary) > 60:
                            snippet += "…"
                        chat_entry["title"] = snippet
        st.session_state.chat_histories[chat_id] = st.session_state.history  # type: ignore[attr-defined]
        _promote_chat(chat_id)


def _render_history() -> None:
    for item in st.session_state.history:  # type: ignore[attr-defined]
        role = item.get("role", "assistant")
        content = item.get("content", "")
        display_role = "user" if role in {"human", "user"} else role
        with st.chat_message(display_role):
            sender_label = item.get("sender")
            if sender_label and sender_label != HUMAN_RECIPIENT:
                st.caption(f"Sender: {sender_label}")
            st.markdown(content or "")
            trace = item.get("trace")
            if trace:
                with st.expander("Trace", expanded=False):
                    st.json(trace)


def _current_autonomous_settings() -> Dict[str, Any]:
    return {
        "max_iterations": int(st.session_state.get("autonomous_max_iterations", 1000)),
        "max_hours": float(st.session_state.get("autonomous_max_hours", 48.0)),
        "max_agents": int(st.session_state.get("autonomous_max_agents", 4)),
    }


def _persist_autonomous_settings(chat_id: str | None) -> None:
    if not chat_id:
        return
    settings_map = st.session_state.autonomous_chat_settings  # type: ignore[attr-defined]
    if not isinstance(settings_map, dict):
        settings_map = {}
        st.session_state.autonomous_chat_settings = settings_map  # type: ignore[attr-defined]
    supervisors = st.session_state.chat_supervisors  # type: ignore[attr-defined]
    if not isinstance(supervisors, dict):
        supervisors = {}
        st.session_state.chat_supervisors = supervisors  # type: ignore[attr-defined]
    supervisors[chat_id] = _active_supervisor()
    if st.session_state.autonomous_mode_enabled:
        settings_map[chat_id] = _current_autonomous_settings()
    else:
        settings_map.pop(chat_id, None)


def _send_message(client: redis.Redis, message: str, reply_to: str | None) -> str:
    ticket_id = uuid.uuid4().hex
    payload = {"chat_id": st.session_state.chat_id, "ticket_id": ticket_id}
    # Orchestrator owns routing. The UI does not set reply_to.
    supervisor = _active_supervisor()
    payload["supervisor"] = supervisor

    if st.session_state.autonomous_mode_enabled:
        settings = _current_autonomous_settings()
        payload["mode"] = "autonomous"
        payload["autonomous_settings"] = settings
        payload.setdefault("task_goal", message)
        _persist_autonomous_settings(st.session_state.chat_id)
    else:
        _persist_autonomous_settings(st.session_state.chat_id)

    envelope = MessageEnvelope(
        sender=supervisor,
        recipient=ORCHESTRATOR_RECIPIENT,
        message=message,
        payload=payload,
    )
    client.xadd(MAILBOX_ORCHESTRATOR, envelope.to_stream_fields())
    return ticket_id


def _send_emergency_stop(client: redis.Redis) -> None:
    ticket_id = uuid.uuid4().hex
    payload = {"chat_id": st.session_state.chat_id, "ticket_id": ticket_id}
    payload["autonomous_control"] = {"action": "emergency_stop"}
    supervisor = _active_supervisor()
    payload["supervisor"] = supervisor
    if st.session_state.autonomous_mode_enabled:
        payload["mode"] = "autonomous"
        payload["autonomous_settings"] = _current_autonomous_settings()
        payload.setdefault("task_goal", "Emergency stop")

    envelope = MessageEnvelope(
        sender=supervisor,
        recipient=ORCHESTRATOR_RECIPIENT,
        message="Emergency stop requested by human.",
        payload=payload,
    )
    client.xadd(MAILBOX_ORCHESTRATOR, envelope.to_stream_fields())
    _append_history("user", "‼️ Emergency stop requested", ticket_id=ticket_id, sender=supervisor)


def _update_workspace_descriptor(chat_id: str | None, payload: Dict[str, Any]) -> None:
    if not chat_id:
        return
    descriptor = payload.get("workspace")
    if not isinstance(descriptor, dict):
        return
    workspace_map = st.session_state.workspace_descriptors  # type: ignore[attr-defined]
    if not isinstance(workspace_map, dict):
        workspace_map = {}
        st.session_state.workspace_descriptors = workspace_map  # type: ignore[attr-defined]
    workspace_map[chat_id] = descriptor


def _push_workspace_message(kind: str, message: str) -> None:
    messages = st.session_state.workspace_messages  # type: ignore[attr-defined]
    if not isinstance(messages, list):
        messages = []
    messages.append(
        {
            "kind": kind,
            "message": message,
            "timestamp": time.time(),
        }
    )
    st.session_state.workspace_messages = messages  # type: ignore[attr-defined]


def _active_workspace_descriptor() -> Optional[Dict[str, Any]]:
    workspace_map = st.session_state.workspace_descriptors  # type: ignore[attr-defined]
    chat_id = st.session_state.chat_id
    if isinstance(workspace_map, dict) and isinstance(chat_id, str):
        descriptor = workspace_map.get(chat_id)
        if isinstance(descriptor, dict):
            return descriptor
    return None


def _invoke_workspace_tool(
    tool: Any,
    params: Dict[str, Any],
) -> tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    try:
        raw_result = tool.invoke(params)
    except Exception as exc:  # noqa: BLE001
        return False, None, str(exc)

    if isinstance(raw_result, dict):
        parsed = raw_result
    else:
        try:
            parsed = json.loads(raw_result or "{}")
        except json.JSONDecodeError:
            return False, None, "Workspace tool returned invalid JSON payload."

    success = bool(parsed.get("success"))
    if success:
        return True, parsed, None

    error_message = parsed.get("error")
    if not error_message:
        errors = parsed.get("errors")
        if isinstance(errors, list):
            error_message = "; ".join(str(entry) for entry in errors if entry)
    if not error_message and isinstance(parsed.get("message"), str):
        error_message = str(parsed["message"])
    if not error_message:
        error_message = "Workspace operation failed."
    return False, parsed, error_message


def _handle_workspace_create(chat_id: str) -> None:
    success, data, error_message = _invoke_workspace_tool(
        workspace_create,
        {"workspace_id": chat_id},
    )
    if success and isinstance(data, dict):
        descriptor = data.get("workspace")
        if isinstance(descriptor, dict):
            workspace_map = st.session_state.workspace_descriptors  # type: ignore[attr-defined]
            if not isinstance(workspace_map, dict):
                workspace_map = {}
                st.session_state.workspace_descriptors = workspace_map  # type: ignore[attr-defined]
            workspace_map[chat_id] = descriptor
            _push_workspace_message(
                "success",
                f"Workspace ready (image: {descriptor.get('image', 'unknown')}).",
            )
        else:
            _push_workspace_message("warning", "Workspace created but descriptor was not returned.")
    else:
        detail = error_message or "unknown error"
        _push_workspace_message("error", f"Failed to create workspace: {detail}")
    st.rerun()
    return None


def _handle_workspace_destroy(chat_id: str) -> None:
    success, data, error_message = _invoke_workspace_tool(
        workspace_destroy,
        {
            "workspace_id": chat_id,
            "delete_volume": True,
        },
    )
    workspace_map = st.session_state.workspace_descriptors  # type: ignore[attr-defined]
    if isinstance(workspace_map, dict):
        workspace_map.pop(chat_id, None)
    selection_map = st.session_state.workspace_log_selection  # type: ignore[attr-defined]
    if isinstance(selection_map, dict):
        selection_map.pop(chat_id, None)

    if success:
        details = []
        if isinstance(data, dict):
            if data.get("container_removed"):
                details.append("container removed")
            if data.get("volume_removed"):
                details.append("volume removed")
        detail_text = f" ({', '.join(details)})" if details else ""
        _push_workspace_message("success", f"Workspace destroyed{detail_text}.")
    else:
        detail = error_message or "unknown error"
        _push_workspace_message("error", f"Failed to destroy workspace: {detail}")
    st.rerun()
    return None


def _handle_workspace_copy_out(chat_id: str, source: str, destination: str) -> None:
    clean_source = source.strip()
    if not clean_source:
        _push_workspace_message("error", "Source path inside the workspace is required.")
        st.rerun()
        return None
    dest_path = Path(destination.strip() or destination)
    if not destination.strip():
        dest_path = WORKSPACE_EXPORT_ROOT / chat_id
    dest_path = dest_path.expanduser()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    params = {
        "workspace_id": chat_id,
        "source_path": clean_source,
        "destination_path": str(dest_path),
    }
    success, data, error_message = _invoke_workspace_tool(workspace_copy_from, params)
    if success and isinstance(data, dict):
        copy_result = data.get("workspace_copy")
        if isinstance(copy_result, dict):
            transferred = copy_result.get("bytes_transferred")
        else:
            transferred = None
        extra = ""
        if isinstance(transferred, int):
            extra = f" ({transferred} bytes)"
        _push_workspace_message(
            "success",
            f"Copied {clean_source} → {dest_path}{extra}.",
        )
    else:
        detail = error_message or "unknown error"
        _push_workspace_message("error", f"Failed to copy out {clean_source}: {detail}")
    st.rerun()
    return None


def _invoke_workspace_exec(chat_id: str, command: str) -> tuple[bool, str, str]:
    success, data, error_message = _invoke_workspace_tool(
        workspace_exec,
        {
            "workspace_id": chat_id,
            "command": command,
        },
    )
    stdout = ""
    stderr = ""
    if isinstance(data, dict):
        command_result = data.get("workspace_command")
        if isinstance(command_result, dict):
            stdout = command_result.get("stdout", "") or ""
            stderr = command_result.get("stderr", "") or ""
    if success:
        return True, stdout, stderr
    fallback = error_message or stderr
    return False, stdout, fallback


def _list_workspace_logs(chat_id: str) -> List[str]:
    command = (
        "if [ -d /workspace/logs ]; then "
        f"ls -1t /workspace/logs | head -{WORKSPACE_LOG_LIST_LIMIT}; "
        "fi"
    )
    success, stdout, _ = _invoke_workspace_exec(chat_id, command)
    if not success and not stdout:
        return []
    return [line.strip() for line in stdout.splitlines() if line.strip()]


def _read_workspace_log(chat_id: str, log_name: str) -> tuple[bool, str]:
    if not log_name:
        return False, "Select a log file to preview."
    safe_path = shlex.quote(f"/workspace/logs/{log_name}")
    command = (
        f"if [ -f {safe_path} ]; then "
        f"tail -n {WORKSPACE_LOG_TAIL_LINES} {safe_path}; "
        "else "
        f"echo 'Log not found: {log_name}' >&2; "
        "exit 1; "
        "fi"
    )
    success, stdout, error_message = _invoke_workspace_exec(chat_id, command)
    if success:
        return True, stdout or "(log is empty)"
    detail = error_message or "Failed to load log file."
    return False, detail


def _workspace_stream_key(workspace_id: str) -> str:
    suffix = workspace_id.strip()
    if not suffix:
        return ""
    return f"{WORKSPACE_STREAM_PREFIX}:{suffix}:events"


def _load_workspace_events(client: redis.Redis, workspace_id: str, limit: int) -> List[Dict[str, Any]]:
    if limit <= 0:
        return []
    stream_key = _workspace_stream_key(workspace_id)
    if not stream_key:
        return []
    try:
        raw_entries = client.xrevrange(stream_key, count=limit)
    except redis.ResponseError:
        return []
    except redis.RedisError as exc:  # noqa: BLE001
        st.error(f"Failed to read workspace events: {exc}")
        return []

    parsed: List[Dict[str, Any]] = []
    for entry_id, fields in raw_entries:
        event = fields.get("event", "unknown")
        timestamp = fields.get("timestamp")
        payload_raw = fields.get("payload")
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {"raw_payload": payload_raw}
        parsed.append(
            {
                "id": entry_id,
                "event": event,
                "timestamp": timestamp,
                "payload": payload,
            }
        )
    return parsed


def _summarize_workspace_event(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict) or not payload:
        return ""
    for key in ("message", "summary", "description", "command"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if "destination" in payload and "source" in payload:
        return f"{payload['source']} → {payload['destination']}"
    items = []
    for key, value in list(payload.items())[:3]:
        if isinstance(value, (str, int, float)):
            items.append(f"{key}={value}")
    summary = ", ".join(items)
    if summary:
        return summary
    return json.dumps(payload, separators=(",", ":"))[:200]


def _poll_updates(client: redis.Redis) -> List[MessageEnvelope]:
    last_id = st.session_state.last_seen_id
    mailbox = _supervisor_mailbox()
    try:
        # Non-blocking read: omit BLOCK to avoid hanging the Streamlit render thread.
        responses = client.xread({mailbox: last_id}, count=50)
    except redis.RedisError as exc:  # noqa: BLE001
        st.warning(f"Redis read error: {exc}")
        return []
    matched: List[MessageEnvelope] = []
    new_last_id = last_id

    for stream_key, entries in responses:
        if stream_key != mailbox:
            continue
        for entry_id, fields in entries:
            envelope = MessageEnvelope.from_stream_fields(fields)
            payload = envelope.payload or {}
            if payload.get("chat_id") != st.session_state.chat_id:
                continue
            _update_workspace_descriptor(st.session_state.chat_id, payload)
            matched.append(envelope)
            if entry_id > new_last_id:
                new_last_id = entry_id

    if new_last_id != last_id:
        st.session_state.last_seen_id = new_last_id
        chat_id = st.session_state.chat_id
        if chat_id:
            _set_last_seen(chat_id, _active_supervisor(), new_last_id)

    return matched


@st.cache_data(ttl=5.0, show_spinner=False)
def _registry_snapshot() -> Dict[str, Any]:
    if UI_BARE:
        return {"agents": [], "stats": None, "error": "bare mode"}
    if not AGENT_REGISTRY_URL:
        return {"agents": [], "stats": None, "error": "AGENT_REGISTRY_URL not set"}

    base = AGENT_REGISTRY_URL.rstrip("/")
    snapshot: Dict[str, Any] = {"agents": [], "stats": None, "error": None}
    try:
        agents_resp = httpx.get(f"{base}/agents", timeout=2.0)
        agents_resp.raise_for_status()
        agents_data = agents_resp.json()
        snapshot["agents"] = agents_data.get("agents", [])
    except Exception as exc:  # noqa: BLE001
        snapshot["error"] = f"Failed to load agents: {exc}"
        return snapshot

    try:
        stats_resp = httpx.get(f"{base}/stats", timeout=2.0)
        stats_resp.raise_for_status()
        snapshot["stats"] = stats_resp.json()
    except Exception as exc:  # noqa: BLE001
        snapshot["error"] = f"Failed to load stats: {exc}"

    return snapshot


def _list_mailboxes(client: redis.Redis) -> List[str]:
    try:
        keys = sorted({key for key in client.scan_iter(f"{MAILBOX_PREFIX}*")})
    except redis.RedisError as exc:  # noqa: BLE001
        st.error(f"Unable to list mailboxes: {exc}")
        return []
    return keys


def _render_stream_view(client: redis.Redis) -> None:
    mailboxes = _list_mailboxes(client)
    if not mailboxes:
        st.info("No mailboxes discovered yet.")
        return

    supervisor_mailbox = _supervisor_mailbox()
    default_index = mailboxes.index(supervisor_mailbox) if supervisor_mailbox in mailboxes else 0
    selected_stream = st.selectbox(
        "Select stream",
        options=mailboxes,
        index=default_index,
        key="stream_select",
    )

    cols = st.columns([1, 1])
    with cols[0]:
        count = st.slider(
            "Entries",
            min_value=10,
            max_value=200,
            step=10,
            value=int(st.session_state.get("stream_count", 50)),
            key="stream_count_slider",
        )
        st.session_state.stream_count = count
    with cols[1]:
        order = st.selectbox(
            "Order",
            options=["Newest to oldest", "Oldest to newest"],
            key="stream_order",
        )

    if st.button("Refresh stream", type="primary"):
        st.rerun()

    try:
        entries = client.xrevrange(selected_stream, count=count)
    except redis.RedisError as exc:  # noqa: BLE001
        st.error(f"Failed to read stream: {exc}")
        return

    if order == "Oldest to newest":
        entries = list(reversed(entries))

    if not entries:
        st.info("No entries in this stream yet.")
        return

    for entry_id, fields in entries:
        with st.expander(entry_id, expanded=False):
            st.json(fields)


def _load_context_metrics(client: redis.Redis, limit: int) -> List[Dict[str, Any]]:
    try:
        raw_entries = client.xrevrange(CONTEXT_METRICS_STREAM, count=limit)
    except redis.ResponseError:
        return []
    except redis.RedisError as exc:  # noqa: BLE001
        st.error(f"Failed to read context metrics: {exc}")
        return []

    parsed: List[Dict[str, Any]] = []
    for entry_id, fields in raw_entries:
        event = fields.get("event", "unknown")
        timestamp = fields.get("timestamp")
        payload_raw = fields.get("payload")
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {"raw_payload": payload_raw}
        parsed.append(
            {
                "id": entry_id,
                "event": event,
                "timestamp": timestamp,
                "payload": payload,
            }
        )

    return list(reversed(parsed))


def _load_autonomous_events(client: redis.Redis, limit: int) -> List[Dict[str, Any]]:
    try:
        raw_entries = client.xrevrange(AUTONOMOUS_EVENTS_STREAM, count=limit)
    except redis.ResponseError:
        return []
    except redis.RedisError as exc:  # noqa: BLE001
        st.error(f"Failed to read autonomous events: {exc}")
        return []

    parsed: List[Dict[str, Any]] = []
    for entry_id, fields in raw_entries:
        event = fields.get("event", "unknown")
        timestamp = fields.get("timestamp")
        payload_raw = fields.get("payload")
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {"raw_payload": payload_raw}
        parsed.append(
            {
                "id": entry_id,
                "event": event,
                "timestamp": timestamp,
                "payload": payload,
            }
        )

    return list(reversed(parsed))


def _render_metrics_dashboard(client: redis.Redis) -> None:
    limit = st.slider(
        "History depth",
        min_value=50,
        max_value=CONTEXT_METRICS_LIMIT,
        step=50,
        value=min(150, CONTEXT_METRICS_LIMIT),
        key="metrics_history_depth",
    )

    if st.button("Refresh metrics", type="primary"):
        st.rerun()

    entries = _load_context_metrics(client, limit=limit)
    if not entries:
        st.info("No context metrics have been emitted yet.")
        return

    latest = entries[-1]
    latest_payload = latest.get("payload", {})
    latest_quality = latest_payload.get("quality_score")
    latest_focus = latest_payload.get("focus_metric") or "—"
    latest_window = latest_payload.get("context_window_used")

    cols = st.columns(3)
    cols[0].metric("Quality Score", f"{latest_quality:.2f}" if isinstance(latest_quality, (int, float)) else "n/a")
    cols[1].metric("Focus Metric", latest_focus)
    cols[2].metric("Context Tokens", latest_window or 0)

    quality_points = [
        {
            "timestamp": entry["timestamp"],
            "quality": entry["payload"].get("quality_score"),
        }
        for entry in entries
        if entry["payload"].get("quality_score") is not None
    ]

    if quality_points:
        st.subheader("Quality Trend")
        st.vega_lite_chart(
            {
                "data": {"values": quality_points},
                "mark": {"type": "line", "interpolate": "monotone"},
                "encoding": {
                    "x": {"field": "timestamp", "type": "temporal", "title": "Time"},
                    "y": {"field": "quality", "type": "quantitative", "title": "Quality"},
                },
            },
            width="stretch",
        )

    window_points = [
        {
            "timestamp": entry["timestamp"],
            "tokens": entry["payload"].get("context_window_used"),
        }
        for entry in entries
        if entry["payload"].get("context_window_used") is not None
    ]

    if window_points:
        st.subheader("Context Window Usage")
        st.vega_lite_chart(
            {
                "data": {"values": window_points},
                "mark": {"type": "area", "line": True},
                "encoding": {
                    "x": {"field": "timestamp", "type": "temporal", "title": "Time"},
                    "y": {"field": "tokens", "type": "quantitative", "title": "Tokens"},
                },
            },
            width="stretch",
        )

    operations = Counter(
        entry["payload"].get("operation")
        for entry in entries
        if entry["event"] == "tool_response" and entry["payload"].get("operation")
    )
    if operations:
        st.subheader("Operation Distribution")
        chart_data = [
            {"operation": op, "count": count} for op, count in sorted(operations.items(), key=lambda item: item[1], reverse=True)
        ]
        st.vega_lite_chart(
            {
                "data": {"values": chart_data},
                "mark": "bar",
                "encoding": {
                    "x": {"field": "operation", "type": "nominal", "title": "Operation"},
                    "y": {"field": "count", "type": "quantitative", "title": "Count"},
                },
            },
            width="stretch",
        )

    with st.expander("Raw Metrics", expanded=False):
        st.json(entries[-50:])


def _render_autonomous_dashboard(client: redis.Redis) -> None:
    limit = st.slider(
        "Event history depth",
        min_value=50,
        max_value=AUTONOMOUS_EVENTS_LIMIT,
        step=50,
        value=min(200, AUTONOMOUS_EVENTS_LIMIT),
        key="autonomous_events_limit",
    )

    events = _load_autonomous_events(client, limit)
    if not events:
        st.info("No autonomous events recorded yet.")
        return

    latest = events[-1]
    st.subheader("Latest Event")
    cols = st.columns(3)
    cols[0].metric("Type", latest.get("event", "unknown"))
    cols[1].metric("Timestamp", latest.get("timestamp", ""))
    payload = latest.get("payload", {})
    thread_id = payload.get("thread_id") if isinstance(payload, dict) else None
    cols[2].metric("Thread", thread_id or "—")

    counts: Dict[str, int] = {}
    milestones: Dict[int, Dict[str, Any]] = {}
    escalations: List[Dict[str, Any]] = []
    critiques: List[Dict[str, Any]] = []
    guardrails: List[Dict[str, Any]] = []

    for item in events:
        event_type = item.get("event", "unknown")
        counts[event_type] = counts.get(event_type, 0) + 1
        payload = item.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if event_type == "checkpoint":
            record = payload.get("record")
            if isinstance(record, dict):
                milestone_id = int(record.get("milestone", 0))
                milestones[milestone_id] = record
        elif event_type == "escalation":
            record = payload.get("record")
            if isinstance(record, dict):
                escalations.append(record)
        elif event_type == "critique":
            record = payload.get("record")
            if isinstance(record, dict):
                critiques.append(record)
        elif event_type == "guardrail_trigger":
            guardrails.append(payload)

    st.subheader("Event Counts")
    count_rows = [
        {"event": key, "count": value}
        for key, value in sorted(counts.items(), key=lambda item: item[0])
    ]
    st.dataframe(count_rows, hide_index=True, use_container_width=True)

    if milestones:
        st.subheader("Milestone Status")
        ordered = [milestones[key] for key in sorted(milestones.keys())]
        st.dataframe(ordered, hide_index=True, use_container_width=True)

    if guardrails:
        st.subheader("Guardrail Events")
        guardrail_rows: List[Dict[str, Any]] = []
        for entry in guardrails[-10:]:
            guardrail_rows.append(
                {
                    "type": entry.get("type", "unknown"),
                    "iteration": entry.get("iteration_count"),
                    "iteration_limit": entry.get("limit"),
                    "elapsed_hours": entry.get("elapsed_hours"),
                    "runtime_limit": entry.get("limit_hours"),
                    "thread": entry.get("thread_id"),
                }
            )
        st.dataframe(guardrail_rows, hide_index=True, use_container_width=True)

    if escalations:
        st.subheader("Escalations")
        st.json(escalations[-5:])

    if critiques:
        st.subheader("Recent Critiques")
        st.json(critiques[-5:])

    with st.expander("Raw Events", expanded=False):
        st.json(events[-50:])


def _queue_session_rerun() -> None:
    ctx = get_script_run_ctx()
    if ctx is None or ctx.script_requests is None:
        return

    rerun_data = RerunData(
        query_string=ctx.query_string,
        page_script_hash=ctx.page_script_hash,
        is_auto_rerun=True,
    )
    ctx.script_requests.request_rerun(rerun_data)


def _ensure_mailbox_watcher(client: redis.Redis) -> None:
    if UI_BARE:
        return
    thread: Thread | None = st.session_state.get("_mailbox_watcher_thread")  # type: ignore[attr-defined]
    stop_event: Event | None = st.session_state.get("_mailbox_watcher_stop")  # type: ignore[attr-defined]

    if thread and thread.is_alive():
        return

    # Ensure there is an active Streamlit run context before wiring the watcher
    ctx = get_script_run_ctx()
    if ctx is None:
        return

    if stop_event is None:
        stop_event = Event()
        st.session_state["_mailbox_watcher_stop"] = stop_event  # type: ignore[attr-defined]

    def _watch() -> None:
        known_chat: str | None = st.session_state.get("chat_id")
        last_id: str = st.session_state.get("last_seen_id", "0-0")
        known_mailbox: str = _supervisor_mailbox()

        while not stop_event.is_set():
            active_chat = st.session_state.get("chat_id")
            if not active_chat:
                time.sleep(0.25)
                continue

            mailbox = _supervisor_mailbox()

            if mailbox != known_mailbox:
                known_mailbox = mailbox
                last_id = st.session_state.get("last_seen_id", "0-0")

            if active_chat != known_chat:
                last_id = st.session_state.get("last_seen_id", "0-0")
                known_chat = active_chat

            try:
                responses = client.xread({mailbox: last_id}, block=5000, count=50)
            except redis.RedisError:
                time.sleep(1.0)
                continue

            if not responses:
                continue

            newest_id = last_id
            has_update = False
            for stream_key, entries in responses:
                if stream_key != mailbox:
                    continue
                for entry_id, fields in entries:
                    envelope = MessageEnvelope.from_stream_fields(fields)
                    payload = envelope.payload or {}
                    if payload.get("chat_id") != active_chat:
                        continue
                    has_update = True
                    if entry_id > newest_id:
                        newest_id = entry_id

            if has_update:
                last_id = newest_id
                _queue_session_rerun()

    watcher = Thread(target=_watch, name="mailbox-watcher", daemon=True)
    # Attach the current ScriptRunContext to the background thread so it can
    # safely interact with Streamlit runtime (session_state, rerun requests).
    add_script_run_ctx(watcher)
    watcher.start()
    st.session_state["_mailbox_watcher_thread"] = watcher  # type: ignore[attr-defined]


def main() -> None:
    st.set_page_config(page_title="Quadracode UI", layout="wide")
    _ensure_session_defaults()

    client = get_redis_client()
    try:
        client.ping()
    except redis.RedisError as exc:  # noqa: BLE001
        st.error(f"Unable to reach Redis: {exc}")
        st.stop()

    if not st.session_state.chats:  # type: ignore[attr-defined]
        _create_chat(client)
    else:
        active_id = st.session_state.chat_id or st.session_state.chats[0]["id"]  # type: ignore[attr-defined]
        _set_active_chat(active_id, client=client)

    _ensure_mailbox_watcher(client)

    updates = _poll_updates(client)
    for envelope in updates:
        trace_payload = envelope.payload.get("messages")
        trace_list = trace_payload if isinstance(trace_payload, list) else None
        _append_history(
            "assistant",
            envelope.message,
            ticket_id=envelope.payload.get("ticket_id"),
            trace=trace_list,
            sender=envelope.sender,
        )

    chat_tab, streams_tab, metrics_tab, autonomous_tab = st.tabs([
        "Chat",
        "Streams",
        "Context Metrics",
        "Autonomous",
    ])

    with st.sidebar:
        st.markdown("### Chats")
        if st.button("＋ New chat", use_container_width=True):
            _create_chat(client)
            st.rerun()

        chat_options = [chat["id"] for chat in st.session_state.chats]  # type: ignore[attr-defined]
        titles_map = {chat["id"]: chat.get("title", "Untitled chat") for chat in st.session_state.chats}  # type: ignore[attr-defined]
        current_chat_id = st.session_state.chat_id
        current_index = chat_options.index(current_chat_id) if current_chat_id in chat_options else 0
        selected_chat = st.radio(
            "Conversations",
            options=chat_options,
            index=current_index,
            format_func=lambda cid: titles_map.get(cid, cid),
            key="chat_selector",
        )
        if selected_chat != current_chat_id:
            _set_active_chat(selected_chat, client=client)
            st.rerun()

        current_entry = _get_chat_entry(st.session_state.chat_id)
        if current_entry is None:
            current_entry = {"id": st.session_state.chat_id, "title": "Untitled chat"}
        title_value = st.text_input(
            "Chat title",
            value=current_entry.get("title", "Untitled chat"),
            key=f"title-input-{st.session_state.chat_id}",
        )
        normalized_title = title_value.strip() or "Untitled chat"
        if normalized_title != current_entry.get("title"):
            current_entry["title"] = normalized_title
            _promote_chat(current_entry["id"])

        st.caption(f"Chat ID: {st.session_state.chat_id}")

        st.divider()

        st.header("Autonomous Mode")
        prev_supervisor = _active_supervisor()
        auto_enabled = st.toggle(
            "Enable HUMAN_OBSOLETE mode",
            value=st.session_state.autonomous_mode_enabled,
            key="autonomous_mode_toggle",
        )
        st.session_state.autonomous_mode_enabled = auto_enabled

        desired_supervisor = HUMAN_CLONE_RECIPIENT if auto_enabled else HUMAN_RECIPIENT
        if desired_supervisor != prev_supervisor:
            _set_supervisor(desired_supervisor, st.session_state.chat_id)
            _reset_last_seen_for_active_chat(client)

        if auto_enabled:
            st.number_input(
                "Max iterations",
                min_value=10,
                max_value=5000,
                step=10,
                value=int(st.session_state.autonomous_max_iterations),
                key="autonomous_max_iterations_input",
            )
            st.number_input(
                "Max runtime (hours)",
                min_value=1.0,
                max_value=168.0,
                step=1.0,
                value=float(st.session_state.autonomous_max_hours),
                key="autonomous_max_hours_input",
            )
            st.number_input(
                "Max agents",
                min_value=1,
                max_value=20,
                step=1,
                value=int(st.session_state.autonomous_max_agents),
                key="autonomous_max_agents_input",
            )

            st.session_state.autonomous_max_iterations = int(st.session_state.autonomous_max_iterations_input)
            st.session_state.autonomous_max_hours = float(st.session_state.autonomous_max_hours_input)
            st.session_state.autonomous_max_agents = int(st.session_state.autonomous_max_agents_input)
        else:
            st.caption("Autonomous controls disabled for this chat.")

        _persist_autonomous_settings(st.session_state.chat_id)

        if st.session_state.autonomous_mode_enabled:
            if st.button("Emergency Stop", type="secondary", use_container_width=True):
                _send_emergency_stop(client)
                st.session_state.autonomous_mode_enabled = False
                st.session_state.autonomous_mode_toggle = False
                _persist_autonomous_settings(st.session_state.chat_id)
                st.rerun()

        st.divider()

        st.header("Workspace")
        workspace_messages = st.session_state.workspace_messages  # type: ignore[attr-defined]
        if isinstance(workspace_messages, list) and workspace_messages:
            for entry in workspace_messages:
                kind = entry.get("kind")
                message = entry.get("message")
                if not isinstance(message, str):
                    continue
                if kind == "success":
                    st.success(message)
                elif kind == "warning":
                    st.warning(message)
                elif kind == "error":
                    st.error(message)
                else:
                    st.info(message)
            st.session_state.workspace_messages = []  # type: ignore[attr-defined]

        active_workspace = _active_workspace_descriptor()
        chat_id = st.session_state.chat_id

        action_cols = st.columns(2)
        create_disabled = not isinstance(chat_id, str) or active_workspace is not None
        destroy_disabled = not isinstance(chat_id, str) or active_workspace is None
        if action_cols[0].button(
            "Create Workspace",
            use_container_width=True,
            disabled=create_disabled,
        ):
            if isinstance(chat_id, str):
                _handle_workspace_create(chat_id)
        if action_cols[1].button(
            "Destroy Workspace",
            type="secondary",
            use_container_width=True,
            disabled=destroy_disabled,
        ):
            if isinstance(chat_id, str):
                _handle_workspace_destroy(chat_id)

        if isinstance(active_workspace, dict):
            info_cols = st.columns(2)
            info_cols[0].markdown(f"**Image**\n\n`{active_workspace.get('image', 'unknown')}`")
            info_cols[1].markdown(f"**Mount**\n\n`{active_workspace.get('mount_path', '/workspace')}`")
            st.caption(
                f"Container: `{active_workspace.get('container', 'unknown')}`\n\n"
                f"Volume: `{active_workspace.get('volume', 'unknown')}`"
            )
            with st.expander("Descriptor", expanded=False):
                st.json(active_workspace)

            st.subheader("Copy Out Files")
            default_destination = str((WORKSPACE_EXPORT_ROOT / chat_id).expanduser()) if isinstance(chat_id, str) else ""
            with st.form("workspace_copy_from_host"):
                source_default = "/workspace/"
                source_value = st.text_input(
                    "Source path inside workspace",
                    value=source_default,
                    key=f"workspace_copy_source_{chat_id}",
                    help="Copy files or directories from the workspace volume.",
                )
                destination_value = st.text_input(
                    "Destination path on host",
                    value=default_destination,
                    key=f"workspace_copy_destination_{chat_id}",
                    help="Defaults to a per-chat directory under workspace_exports/ if left blank.",
                )
                submitted = st.form_submit_button("Copy Out")
                if submitted and isinstance(chat_id, str):
                    _handle_workspace_copy_out(chat_id, source_value, destination_value)

            st.subheader("Recent Logs")
            st.caption("Preview files under `/workspace/logs` (newest first).")
            logs = []
            if isinstance(chat_id, str):
                try:
                    logs = _list_workspace_logs(chat_id)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Failed to list logs: {exc}")
                    logs = []
            if logs:
                selection_map = st.session_state.workspace_log_selection  # type: ignore[attr-defined]
                if not isinstance(selection_map, dict):
                    selection_map = {}
                existing_selection = selection_map.get(chat_id) if isinstance(chat_id, str) else None
                default_index = 0
                if isinstance(existing_selection, str) and existing_selection in logs:
                    default_index = logs.index(existing_selection)
                selected_log = st.selectbox(
                    "Select log file",
                    options=logs,
                    index=default_index,
                    key=f"workspace_log_select_{chat_id}",
                    help="Tail view shows the last few hundred lines.",
                )
                if isinstance(chat_id, str):
                    selection_map[chat_id] = selected_log
                    st.session_state.workspace_log_selection = selection_map  # type: ignore[attr-defined]
                    success, content = _read_workspace_log(chat_id, selected_log)
                    if success:
                        st.code(content, language="text")
                    else:
                        st.warning(content)
            else:
                st.caption("No logs available yet.")

            st.subheader("Workspace Events")
            if isinstance(chat_id, str):
                stream_key = _workspace_stream_key(chat_id)
                if stream_key:
                    st.caption(f"Stream: `{stream_key}`")
                limit_key = f"workspace_events_limit_{chat_id}"
                if limit_key not in st.session_state:
                    st.session_state[limit_key] = WORKSPACE_EVENTS_LIMIT
                controls = st.columns([3, 1])
                with controls[0]:
                    limit_value = st.slider(
                        "Events to load",
                        min_value=10,
                        max_value=200,
                        step=10,
                        key=limit_key,
                    )
                with controls[1]:
                    if st.button(
                        "Refresh",
                        key=f"workspace_events_refresh_{chat_id}",
                        use_container_width=True,
                    ):
                        st.rerun()
                events = _load_workspace_events(client, chat_id, int(limit_value))
                if events:
                    event_types = sorted({entry.get("event", "unknown") or "unknown" for entry in events})
                    filter_key = f"workspace_events_filter_{chat_id}"
                    existing_selection = st.session_state.get(filter_key)
                    if isinstance(existing_selection, list):
                        sanitized_selection = [event for event in existing_selection if event in event_types]
                        if not sanitized_selection:
                            sanitized_selection = event_types
                    else:
                        sanitized_selection = event_types
                    st.session_state[filter_key] = sanitized_selection
                    selected_types = st.multiselect(
                        "Event types",
                        options=event_types,
                        key=filter_key,
                        help="Filter workspace events by type.",
                    )
                    if not selected_types:
                        selected_types = event_types
                        st.session_state[filter_key] = selected_types
                    filtered_events = [
                        entry
                        for entry in events
                        if entry.get("event", "unknown") in selected_types
                    ]
                    if filtered_events:
                        summaries: List[Dict[str, Any]] = []
                        for entry in filtered_events:
                            summary_text = _summarize_workspace_event(entry.get("payload", {}))
                            if not summary_text:
                                summary_text = "-"
                            summaries.append(
                                {
                                    "event": entry.get("event", "unknown"),
                                    "timestamp": entry.get("timestamp") or "",
                                    "summary": summary_text,
                                    "id": entry.get("id"),
                                }
                            )
                        st.dataframe(
                            summaries,
                            hide_index=True,
                            use_container_width=True,
                        )
                        with st.expander("Event details", expanded=False):
                            for entry in filtered_events:
                                label_timestamp = entry.get("timestamp") or ""
                                label_event = entry.get("event", "unknown")
                                label_id = entry.get("id", "")
                                st.markdown(f"**{label_timestamp} — {label_event}** `{label_id}`")
                                st.json(entry.get("payload", {}))
                    else:
                        st.caption("No events match the current filters.")
                else:
                    st.caption("No workspace events emitted yet.")
        else:
            st.caption("No workspace descriptor published yet.")

        st.divider()

        st.header("Agent Registry")
        snapshot = _registry_snapshot()
        agents = snapshot.get("agents", [])
        stats = snapshot.get("stats") or {}
        error = snapshot.get("error")

        total_agents = len(agents)
        healthy = 0
        if isinstance(stats, dict):
            healthy = int(stats.get("healthy_agents", 0))
            total_agents = int(stats.get("total_agents", total_agents))

        cols = st.columns(2)
        cols[0].metric("Agents", total_agents)
        cols[1].metric("Healthy", healthy)

        if error:
            st.warning(error)

    with chat_tab:
        st.title("Quadracode Assistant")
        st.caption(
            "Interact with the orchestrator. Messages are routed via Redis Streams."
        )

        _render_history()

        if prompt := st.chat_input("Ask the orchestrator..."):
            ticket = _send_message(client, prompt, reply_to=None)
            _append_history("user", prompt, ticket_id=ticket, sender=_active_supervisor())
            st.rerun()

    with streams_tab:
        st.title("Redis Stream Viewer")
        st.caption("Inspect raw mailbox traffic for debugging and audits.")
        _render_stream_view(client)

    with metrics_tab:
        st.title("Context Metrics Dashboard")
        st.caption("Live metrics emitted by the context engineering node.")
        _render_metrics_dashboard(client)

    with autonomous_tab:
        st.title("Autonomous Mode Events")
        st.caption("Checkpoints, critiques, and escalations emitted during HUMAN_OBSOLETE runs.")
        _render_autonomous_dashboard(client)


if __name__ == "__main__":
    main()

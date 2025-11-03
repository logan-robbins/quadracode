from __future__ import annotations

import json
import os
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
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
    HUMAN_RECIPIENT,
    MAILBOX_PREFIX,
    ORCHESTRATOR_RECIPIENT,
    MessageEnvelope,
)
from quadracode_contracts.messaging import mailbox_key


REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
AGENT_REGISTRY_URL = os.environ.get("AGENT_REGISTRY_URL", "")
UI_BARE = os.environ.get("UI_BARE", "0") == "1"
CONTEXT_METRICS_STREAM = os.environ.get("CONTEXT_METRICS_STREAM", "qc:context:metrics")
CONTEXT_METRICS_LIMIT = int(os.environ.get("CONTEXT_METRICS_LIMIT", "200"))
AUTONOMOUS_EVENTS_STREAM = os.environ.get("AUTONOMOUS_EVENTS_STREAM", "qc:autonomous:events")
AUTONOMOUS_EVENTS_LIMIT = int(os.environ.get("AUTONOMOUS_EVENTS_LIMIT", "200"))

MAILBOX_ORCHESTRATOR = mailbox_key(ORCHESTRATOR_RECIPIENT)
MAILBOX_HUMAN = mailbox_key(HUMAN_RECIPIENT)


@st.cache_resource(show_spinner=False)
def get_redis_client() -> redis.Redis:
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def _ensure_session_defaults() -> None:
    st.session_state.setdefault("chats", [])
    st.session_state.setdefault("chat_histories", {})
    st.session_state.setdefault("chat_last_seen", {})
    st.session_state.setdefault("chat_id", None)
    st.session_state.setdefault("history", [])  # type: ignore[attr-defined]
    st.session_state.setdefault("last_seen_id", "0-0")
    st.session_state.setdefault("chat_selector", None)
    st.session_state.setdefault("autonomous_mode_enabled", False)
    st.session_state.setdefault("autonomous_max_iterations", 1000)
    st.session_state.setdefault("autonomous_max_hours", 48.0)
    st.session_state.setdefault("autonomous_max_agents", 4)
    st.session_state.setdefault("autonomous_chat_settings", {})
    st.session_state.setdefault("autonomous_mode_toggle", False)
    st.session_state.setdefault("autonomous_max_iterations_input", 1000)
    st.session_state.setdefault("autonomous_max_hours_input", 48.0)
    st.session_state.setdefault("autonomous_max_agents_input", 4)


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


def _baseline_last_seen(client: redis.Redis) -> str:
    latest = client.xrevrange(MAILBOX_HUMAN, count=1)
    return latest[0][0] if latest else "0-0"


def _set_active_chat(chat_id: str, *, client: Optional[redis.Redis] = None) -> None:
    entry = _get_chat_entry(chat_id)
    if entry is None:
        return
    histories = st.session_state.chat_histories  # type: ignore[attr-defined]
    last_seen_map = st.session_state.chat_last_seen  # type: ignore[attr-defined]
    history = histories.setdefault(chat_id, [])
    last_seen = last_seen_map.get(chat_id)
    if last_seen is None and client is not None:
        last_seen = _baseline_last_seen(client)
        last_seen_map[chat_id] = last_seen
    st.session_state.chat_id = chat_id
    st.session_state.history = history  # type: ignore[attr-defined]
    st.session_state.last_seen_id = last_seen or "0-0"
    st.session_state.chat_selector = chat_id
    _promote_chat(chat_id)

    settings_map = st.session_state.autonomous_chat_settings  # type: ignore[attr-defined]
    chat_settings = settings_map.get(chat_id, {}) if isinstance(settings_map, dict) else {}
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
    st.session_state.chat_last_seen[chat_id] = _baseline_last_seen(client)  # type: ignore[attr-defined]
    _set_active_chat(chat_id, client=client)
    return chat_id


def _append_history(
    role: str,
    content: str,
    *,
    ticket_id: str | None = None,
    trace: List[Dict[str, Any]] | None = None,
) -> None:
    entry: Dict[str, Any] = {"role": role, "content": content}
    if ticket_id:
        entry["ticket_id"] = ticket_id
    if trace:
        entry["trace"] = trace
    st.session_state.history.append(entry)
    chat_id = st.session_state.chat_id
    if chat_id:
        if role == "human":
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
        with st.chat_message(role):
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
    if st.session_state.autonomous_mode_enabled:
        settings_map[chat_id] = _current_autonomous_settings()
    else:
        settings_map.pop(chat_id, None)


def _send_message(client: redis.Redis, message: str, reply_to: str | None) -> str:
    ticket_id = uuid.uuid4().hex
    payload = {"chat_id": st.session_state.chat_id, "ticket_id": ticket_id}
    # Orchestrator owns routing. The UI does not set reply_to.

    if st.session_state.autonomous_mode_enabled:
        settings = _current_autonomous_settings()
        payload["mode"] = "autonomous"
        payload["autonomous_settings"] = settings
        payload.setdefault("task_goal", message)
        _persist_autonomous_settings(st.session_state.chat_id)
    else:
        _persist_autonomous_settings(st.session_state.chat_id)

    envelope = MessageEnvelope(
        sender=HUMAN_RECIPIENT,
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
    if st.session_state.autonomous_mode_enabled:
        payload["mode"] = "autonomous"
        payload["autonomous_settings"] = _current_autonomous_settings()
        payload.setdefault("task_goal", "Emergency stop")

    envelope = MessageEnvelope(
        sender=HUMAN_RECIPIENT,
        recipient=ORCHESTRATOR_RECIPIENT,
        message="Emergency stop requested by human.",
        payload=payload,
    )
    client.xadd(MAILBOX_ORCHESTRATOR, envelope.to_stream_fields())
    _append_history("human", "‼️ Emergency stop requested", ticket_id=ticket_id)


def _poll_updates(client: redis.Redis) -> List[MessageEnvelope]:
    last_id = st.session_state.last_seen_id
    try:
        # Non-blocking read: omit BLOCK to avoid hanging the Streamlit render thread.
        responses = client.xread({MAILBOX_HUMAN: last_id}, count=50)
    except redis.RedisError as exc:  # noqa: BLE001
        st.warning(f"Redis read error: {exc}")
        return []
    matched: List[MessageEnvelope] = []
    new_last_id = last_id

    for stream_key, entries in responses:
        if stream_key != MAILBOX_HUMAN:
            continue
        for entry_id, fields in entries:
            envelope = MessageEnvelope.from_stream_fields(fields)
            payload = envelope.payload or {}
            if payload.get("chat_id") != st.session_state.chat_id:
                continue
            matched.append(envelope)
            if entry_id > new_last_id:
                new_last_id = entry_id

    if new_last_id != last_id:
        st.session_state.last_seen_id = new_last_id
        chat_id = st.session_state.chat_id
        if chat_id:
            st.session_state.chat_last_seen[chat_id] = new_last_id  # type: ignore[attr-defined]

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

    default_index = mailboxes.index(MAILBOX_HUMAN) if MAILBOX_HUMAN in mailboxes else 0
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

        while not stop_event.is_set():
            active_chat = st.session_state.get("chat_id")
            if not active_chat:
                time.sleep(0.25)
                continue

            if active_chat != known_chat:
                last_id = st.session_state.get("last_seen_id", "0-0")
                known_chat = active_chat

            try:
                responses = client.xread({MAILBOX_HUMAN: last_id}, block=5000, count=50)
            except redis.RedisError:
                time.sleep(1.0)
                continue

            if not responses:
                continue

            newest_id = last_id
            has_update = False
            for stream_key, entries in responses:
                if stream_key != MAILBOX_HUMAN:
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
        auto_enabled = st.toggle(
            "Enable HUMAN_OBSOLETE mode",
            value=st.session_state.autonomous_mode_enabled,
            key="autonomous_mode_toggle",
        )
        st.session_state.autonomous_mode_enabled = auto_enabled

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
            _append_history("human", prompt, ticket_id=ticket)
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

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import redis
import streamlit as st

from quadracode_contracts import (
    HUMAN_RECIPIENT,
    MAILBOX_PREFIX,
    ORCHESTRATOR_RECIPIENT,
    MessageEnvelope,
)
from quadracode_contracts.messaging import mailbox_key


REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
POLL_INTERVAL_MS = int(os.environ.get("UI_POLL_INTERVAL_MS", "2000"))
AGENT_REGISTRY_URL = os.environ.get("AGENT_REGISTRY_URL", "")

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
    st.session_state.setdefault("poll_interval_ms", POLL_INTERVAL_MS)
    st.session_state.setdefault("auto_refresh_enabled", POLL_INTERVAL_MS > 0)
    st.session_state.setdefault("stream_auto_refresh", False)
    st.session_state.setdefault("stream_refresh_ms", 2000)
    st.session_state.setdefault("selected_reply_to", None)
    st.session_state.setdefault("chat_selector", None)


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


def _create_chat(client: redis.Redis, *, title: Optional[str] = None) -> str:
    chat_id = uuid.uuid4().hex
    entry = {
        "id": chat_id,
        "title": title or "New chat",
        "created": datetime.utcnow().isoformat(timespec="seconds"),
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


def _send_message(client: redis.Redis, message: str, reply_to: str | None) -> str:
    ticket_id = uuid.uuid4().hex
    payload = {"chat_id": st.session_state.chat_id, "ticket_id": ticket_id}
    if reply_to:
        payload["reply_to"] = reply_to

    envelope = MessageEnvelope(
        sender=HUMAN_RECIPIENT,
        recipient=ORCHESTRATOR_RECIPIENT,
        message=message,
        payload=payload,
    )
    client.xadd(MAILBOX_ORCHESTRATOR, envelope.to_stream_fields())
    return ticket_id


def _poll_updates(client: redis.Redis) -> List[MessageEnvelope]:
    last_id = st.session_state.last_seen_id
    auto_refresh = bool(st.session_state.get("auto_refresh_enabled", True))
    interval = int(st.session_state.get("poll_interval_ms", POLL_INTERVAL_MS))
    block_ms = max(100, interval if auto_refresh and interval > 0 else 100)

    try:
        responses = client.xread({MAILBOX_HUMAN: last_id}, block=block_ms, count=50)
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

    cols = st.columns([1, 1, 1])
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
    with cols[2]:
        stream_auto = st.checkbox(
            "Auto-refresh",
            value=bool(st.session_state.get("stream_auto_refresh", False)),
            key="stream_auto_refresh_checkbox",
        )
        st.session_state.stream_auto_refresh = stream_auto
        if stream_auto:
            refresh_ms = st.slider(
                "Interval (ms)",
                min_value=1000,
                max_value=10000,
                step=500,
                value=int(st.session_state.get("stream_refresh_ms", 2000)),
                key="stream_refresh_slider",
            )
            st.session_state.stream_refresh_ms = refresh_ms
        else:
            st.session_state.stream_refresh_ms = 0

    if st.button("Refresh stream", type="primary"):
        st.experimental_rerun()

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

    chat_tab, streams_tab = st.tabs(["Chat", "Streams"])

    with st.sidebar:
        st.markdown("### Chats")
        if st.button("＋ New chat", use_container_width=True):
            _create_chat(client)
            st.experimental_rerun()

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
            st.experimental_rerun()

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

        auto_refresh = st.checkbox(
            "Auto refresh",
            value=bool(st.session_state.auto_refresh_enabled),
        )
        st.session_state.auto_refresh_enabled = auto_refresh

        if auto_refresh:
            poll_interval = st.slider(
                "Refresh interval (ms)",
                min_value=500,
                max_value=5000,
                step=500,
                value=int(max(st.session_state.poll_interval_ms, 500)),
            )
            st.session_state.poll_interval_ms = poll_interval
        else:
            st.session_state.poll_interval_ms = 0

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

        agent_ids = [a.get("agent_id") for a in agents if isinstance(a, dict) and a.get("agent_id")]

        def _agent_label(value: str | None) -> str:
            return "Automatic" if value in (None, "") else str(value)

        agent_options = [None, *agent_ids]
        current_reply = st.session_state.get("selected_reply_to")
        index = agent_options.index(current_reply) if current_reply in agent_options else 0
        selected = st.selectbox(
            "Route via agent",
            options=agent_options,
            index=index,
            format_func=_agent_label,
            key="agent_select",
        )

        st.session_state.selected_reply_to = selected

    with chat_tab:
        st.title("Quadracode Assistant")
        st.caption(
            "Interact with the orchestrator. Messages are routed via Redis Streams."
        )

        _render_history()

        if prompt := st.chat_input("Ask the orchestrator..."):
            reply_target = st.session_state.get("selected_reply_to")
            ticket = _send_message(client, prompt, reply_to=reply_target)
            _append_history("human", prompt, ticket_id=ticket)
            st.experimental_rerun()

    with streams_tab:
        st.title("Redis Stream Viewer")
        st.caption("Inspect raw mailbox traffic for debugging and audits.")
        _render_stream_view(client)

    interval = int(st.session_state.get("poll_interval_ms", POLL_INTERVAL_MS))
    if st.session_state.get("auto_refresh_enabled") and interval > 0:
        st.experimental_autorefresh(interval=interval, key="ui-refresh")

    stream_interval = int(st.session_state.get("stream_refresh_ms", 0))
    if st.session_state.get("stream_auto_refresh") and stream_interval > 0:
        st.experimental_autorefresh(interval=stream_interval, key="stream-refresh")


if __name__ == "__main__":
    main()

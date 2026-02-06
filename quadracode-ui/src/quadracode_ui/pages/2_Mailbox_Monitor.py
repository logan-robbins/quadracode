"""
Mailbox Monitor page for Quadracode UI.

This page provides real-time monitoring of all Redis Streams mailbox traffic.
Supports QUADRACODE_MOCK_MODE for standalone testing with simulated streams.

Uses ``@st.fragment(run_every=…)`` for the data-display section so the page
auto-refreshes without the blocking ``time.sleep`` that previously froze the UI.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from quadracode_ui.components.message_list import render_message_card
from quadracode_ui.config import MOCK_MODE, UI_MESSAGE_PAGE_SIZE
from quadracode_ui.utils.message_utils import get_all_messages
from quadracode_ui.utils.redis_client import get_redis_client, list_mailboxes, test_redis_connection


# Page configuration
st.set_page_config(page_title="Mailbox Monitor - Quadracode", page_icon="📡", layout="wide")

# Get Redis client
client = get_redis_client()
success, error = test_redis_connection(client)

if not success:
    st.error(f"❌ Unable to connect to Redis: {error}")
    st.stop()

# Header
st.title("📡 Mailbox Monitor")
st.caption("Real-time view of all Redis Streams traffic")

if MOCK_MODE:
    st.info("🧪 **Mock Mode** - Viewing simulated Redis Streams data")

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    "mailbox_monitor_auto_refresh": True,
    "mailbox_monitor_limit": UI_MESSAGE_PAGE_SIZE,
    "mailbox_monitor_refresh_interval": 5,
    "mailbox_monitor_use_regex": False,
    "selected_message_id": None,
}
for _key, _val in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _val

# ---------------------------------------------------------------------------
# Controls (outside the auto-refresh fragment)
# ---------------------------------------------------------------------------
col1, col2, col3 = st.columns([2, 1, 1])

with col1:
    all_mailboxes = list_mailboxes(client)
    if not all_mailboxes:
        st.warning("No mailboxes discovered yet")
        st.stop()

    selected_mailboxes = st.multiselect(
        "Select mailboxes to monitor",
        options=all_mailboxes,
        default=all_mailboxes,
        help="Choose which mailbox streams to display messages from",
    )

with col2:
    limit = st.number_input(
        "Messages per mailbox",
        min_value=10, max_value=200,
        value=st.session_state.mailbox_monitor_limit, step=10,
    )
    st.session_state.mailbox_monitor_limit = limit

with col3:
    auto_refresh = st.toggle(
        "Auto-refresh",
        value=st.session_state.mailbox_monitor_auto_refresh,
        help="Automatically refresh the view",
    )
    st.session_state.mailbox_monitor_auto_refresh = auto_refresh

    if auto_refresh:
        refresh_interval = st.number_input(
            "Refresh interval (seconds)",
            min_value=1, max_value=60,
            value=st.session_state.mailbox_monitor_refresh_interval, step=1,
        )
        st.session_state.mailbox_monitor_refresh_interval = refresh_interval

    if st.button("🔄 Refresh Now", use_container_width=True):
        st.rerun()

if not selected_mailboxes:
    st.info("Select at least one mailbox to monitor")
    st.stop()

# ---------------------------------------------------------------------------
# Filters (outside the fragment so changes trigger full reruns)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🔍 Filters")

col_toggle1, col_toggle2 = st.columns([1, 3])
with col_toggle1:
    use_regex = st.checkbox(
        "Use regex",
        value=st.session_state.mailbox_monitor_use_regex,
        help="Enable regular expression matching in filters",
    )
    st.session_state.mailbox_monitor_use_regex = use_regex

filter_col1, filter_col2, filter_col3 = st.columns(3)

with filter_col1:
    sender_filter = st.text_input(
        "Filter by sender",
        placeholder="e.g., human, orchestrator" + (" (regex enabled)" if use_regex else ""),
        help="Filter by sender name" + (" - regex patterns supported" if use_regex else ""),
    )

with filter_col2:
    recipient_filter = st.text_input(
        "Filter by recipient",
        placeholder="e.g., orchestrator, agent-.*" + (" (regex)" if use_regex else ""),
        help="Filter by recipient name" + (" - regex patterns supported" if use_regex else ""),
    )

with filter_col3:
    search_text = st.text_input(
        "Search message content",
        placeholder="Search in messages..." + (" (regex)" if use_regex else ""),
        help="Search for text in message content" + (" - regex patterns supported" if use_regex else ""),
    )

# Time range filter
st.markdown("**Time Range Filter**")
time_col1, time_col2, time_col3 = st.columns(3)

time_from = None
time_to = None

with time_col1:
    time_filter_type = st.selectbox(
        "Time filter",
        options=["All time", "Last 5 minutes", "Last 15 minutes", "Last hour", "Last 24 hours", "Custom range"],
        index=0,
    )

with time_col2:
    if time_filter_type == "Custom range":
        time_from = st.time_input(
            "From time (today)",
            value=datetime.now(UTC).replace(hour=0, minute=0, second=0).time(),
        )

with time_col3:
    if time_filter_type == "Custom range":
        time_to = st.time_input(
            "To time (today)",
            value=datetime.now(UTC).time(),
        )

# ---------------------------------------------------------------------------
# Auto-refreshing data section wrapped in a fragment.
#
# When *auto_refresh* is enabled the fragment auto-reruns every
# ``refresh_interval`` seconds, re-fetching messages from Redis and
# redrawing the table/cards/detailed view **without** blocking the
# Streamlit event loop.
# ---------------------------------------------------------------------------
_auto_interval = (
    st.session_state.mailbox_monitor_refresh_interval if auto_refresh else None
)


@st.fragment(run_every=_auto_interval)
def _render_mailbox_data() -> None:  # noqa: C901 – page-level display function
    """Fetch, filter, and display mailbox messages."""
    with st.spinner("Loading messages..."):
        messages = get_all_messages(client, selected_mailboxes, limit=int(limit))

    if not messages:
        st.info("No messages found in selected mailboxes")
        return

    # -- Apply time-range filter --
    filtered_messages = messages

    if time_filter_type != "All time":
        now = datetime.now(UTC)
        cutoff_time: datetime | None = None

        if time_filter_type == "Last 5 minutes":
            cutoff_time = now - timedelta(minutes=5)
        elif time_filter_type == "Last 15 minutes":
            cutoff_time = now - timedelta(minutes=15)
        elif time_filter_type == "Last hour":
            cutoff_time = now - timedelta(hours=1)
        elif time_filter_type == "Last 24 hours":
            cutoff_time = now - timedelta(hours=24)
        elif time_filter_type == "Custom range" and time_from and time_to:
            today = now.date()
            cutoff_time = datetime.combine(today, time_from, tzinfo=UTC)
            end_time = datetime.combine(today, time_to, tzinfo=UTC)
            filtered_messages = [
                m for m in filtered_messages
                if cutoff_time
                <= datetime.fromisoformat(m.get("timestamp", "").replace("Z", "+00:00"))
                <= end_time
            ]
            cutoff_time = None  # already applied

        if cutoff_time is not None:
            filtered_messages = [
                m for m in filtered_messages
                if datetime.fromisoformat(m.get("timestamp", "").replace("Z", "+00:00")) >= cutoff_time
            ]

    # -- Apply sender filter --
    if sender_filter:
        if use_regex:
            try:
                pat = re.compile(sender_filter, re.IGNORECASE)
                filtered_messages = [m for m in filtered_messages if pat.search(m.get("sender", ""))]
            except re.error as exc:
                st.error(f"Invalid regex pattern for sender: {exc}")
        else:
            filtered_messages = [
                m for m in filtered_messages
                if sender_filter.lower() in m.get("sender", "").lower()
            ]

    # -- Apply recipient filter --
    if recipient_filter:
        if use_regex:
            try:
                pat = re.compile(recipient_filter, re.IGNORECASE)
                filtered_messages = [m for m in filtered_messages if pat.search(m.get("recipient", ""))]
            except re.error as exc:
                st.error(f"Invalid regex pattern for recipient: {exc}")
        else:
            filtered_messages = [
                m for m in filtered_messages
                if recipient_filter.lower() in m.get("recipient", "").lower()
            ]

    # -- Apply message content search --
    if search_text:
        if use_regex:
            try:
                pat = re.compile(search_text, re.IGNORECASE)
                filtered_messages = [m for m in filtered_messages if pat.search(m.get("message", ""))]
            except re.error as exc:
                st.error(f"Invalid regex pattern for search: {exc}")
        else:
            filtered_messages = [
                m for m in filtered_messages
                if search_text.lower() in m.get("message", "").lower()
            ]

    # -- Display results --
    st.divider()
    st.subheader(f"Messages ({len(filtered_messages)} found)")

    if not filtered_messages:
        st.warning("No messages match the current filters")
        return

    display_mode = st.radio(
        "Display mode",
        options=["Table", "Cards", "Detailed"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if display_mode == "Table":
        table_data = []
        for i, msg in enumerate(filtered_messages):
            message_preview = msg.get("message", "")[:100]
            if len(msg.get("message", "")) > 100:
                message_preview += "..."
            table_data.append({
                "ID": i,
                "Timestamp": msg.get("timestamp", ""),
                "Mailbox": msg.get("mailbox", "").split("/")[-1],
                "Sender": msg.get("sender", ""),
                "Recipient": msg.get("recipient", ""),
                "Message": message_preview,
            })

        df = pd.DataFrame(table_data)
        st.dataframe(df.drop(columns=["ID"]), use_container_width=True, hide_index=True)

        # Detail panel
        st.divider()
        st.subheader("📋 Message Detail Panel")

        if filtered_messages:
            selected_row = st.number_input(
                "Select message row to view details (0-based index)",
                min_value=0, max_value=len(filtered_messages) - 1, value=0,
                help="Enter the row number from the table above",
            )

            if 0 <= selected_row < len(filtered_messages):
                msg = filtered_messages[selected_row]
                detail_c1, detail_c2 = st.columns(2)

                with detail_c1:
                    st.markdown("**Message ID:**")
                    st.code(msg.get("id", "N/A"), language=None)
                    st.markdown("**Mailbox:**")
                    st.code(msg.get("mailbox", ""), language=None)
                    st.markdown("**Sender:**")
                    st.info(f"👤 {msg.get('sender', '')}")
                    st.markdown("**Recipient:**")
                    st.info(f"📬 {msg.get('recipient', '')}")

                with detail_c2:
                    st.markdown("**Timestamp:**")
                    st.code(msg.get("timestamp", ""), language=None)
                    try:
                        msg_time = datetime.fromisoformat(
                            msg.get("timestamp", "").replace("Z", "+00:00"),
                        )
                        delta = datetime.now(UTC) - msg_time
                        secs = delta.total_seconds()
                        if secs < 60:
                            relative = f"{int(secs)} seconds ago"
                        elif secs < 3600:
                            relative = f"{int(secs / 60)} minutes ago"
                        elif secs < 86400:
                            relative = f"{int(secs / 3600)} hours ago"
                        else:
                            relative = f"{int(secs / 86400)} days ago"
                        st.caption(f"⏱️ {relative}")
                    except (ValueError, AttributeError):
                        pass

                    if st.button("📋 Copy Message ID", key=f"copy_{selected_row}"):
                        st.code(msg.get("id", ""), language=None)
                        st.success("Message ID displayed above for copying")

                st.markdown("---")
                st.markdown("**Message Content:**")
                st.text_area(
                    "Message", value=msg.get("message", ""),
                    height=150, label_visibility="collapsed",
                )
                payload = msg.get("payload")
                if payload:
                    st.markdown("**Payload:**")
                    st.json(payload, expanded=True)

    elif display_mode == "Cards":
        for msg in filtered_messages[:50]:
            render_message_card(msg, show_full=False)
            st.divider()

    else:  # Detailed
        for _i, msg in enumerate(filtered_messages[:50]):
            with st.expander(
                f"{msg.get('timestamp', '')} | {msg.get('sender', '')} → {msg.get('recipient', '')}",
                expanded=False,
            ):
                st.markdown(f"**Mailbox:** `{msg.get('mailbox', '')}`")
                st.markdown(f"**Message ID:** `{msg.get('id', '')}`")
                st.markdown(f"**Sender:** {msg.get('sender', '')}")
                st.markdown(f"**Recipient:** {msg.get('recipient', '')}")
                st.markdown(f"**Timestamp:** {msg.get('timestamp', '')}")
                st.markdown("**Message:**")
                st.text(msg.get("message", ""))
                payload = msg.get("payload")
                if payload:
                    st.markdown("**Payload:**")
                    st.json(payload)

    # -- Sidebar statistics --
    with st.sidebar:
        st.header("Statistics")

        sender_counts: dict[str, int] = {}
        for msg in filtered_messages:
            s = msg.get("sender", "unknown")
            sender_counts[s] = sender_counts.get(s, 0) + 1

        st.subheader("Messages by Sender")
        for sender, count in sorted(sender_counts.items(), key=lambda x: x[1], reverse=True):
            st.metric(sender, count)

        st.divider()

        recipient_counts: dict[str, int] = {}
        for msg in filtered_messages:
            r = msg.get("recipient", "unknown")
            recipient_counts[r] = recipient_counts.get(r, 0) + 1

        st.subheader("Messages by Recipient")
        for recipient, count in sorted(recipient_counts.items(), key=lambda x: x[1], reverse=True):
            st.metric(recipient, count)

        st.divider()

        st.subheader("Mailbox Health")
        for mailbox in selected_mailboxes:
            try:
                stream_len = client.xlen(mailbox)
                if stream_len > 0:
                    st.success(f"✓ {mailbox.split('/')[-1]}: {stream_len} messages")
                else:
                    st.warning(f"○ {mailbox.split('/')[-1]}: Empty")
            except Exception as exc:  # noqa: BLE001
                st.error(f"✗ {mailbox.split('/')[-1]}: Error - {exc}")


_render_mailbox_data()



"""
Mailbox Monitor page for Quadracode UI.

This page provides real-time monitoring of all Redis Streams mailbox traffic.
"""

import pandas as pd
import streamlit as st

from quadracode_ui.components.message_list import render_message_card
from quadracode_ui.config import UI_MESSAGE_PAGE_SIZE
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

# Initialize session state
if "mailbox_monitor_auto_refresh" not in st.session_state:
    st.session_state.mailbox_monitor_auto_refresh = True
if "mailbox_monitor_limit" not in st.session_state:
    st.session_state.mailbox_monitor_limit = UI_MESSAGE_PAGE_SIZE

# Controls
col1, col2, col3 = st.columns([2, 1, 1])

with col1:
    # Discover all mailboxes
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
        min_value=10,
        max_value=200,
        value=st.session_state.mailbox_monitor_limit,
        step=10,
    )
    st.session_state.mailbox_monitor_limit = limit

with col3:
    auto_refresh = st.toggle(
        "Auto-refresh",
        value=st.session_state.mailbox_monitor_auto_refresh,
        help="Automatically refresh the view",
    )
    st.session_state.mailbox_monitor_auto_refresh = auto_refresh
    
    if st.button("🔄 Refresh Now", use_container_width=True):
        st.rerun()

if not selected_mailboxes:
    st.info("Select at least one mailbox to monitor")
    st.stop()

# Filter controls
st.divider()
filter_col1, filter_col2, filter_col3 = st.columns(3)

with filter_col1:
    sender_filter = st.text_input(
        "Filter by sender",
        placeholder="e.g., human, orchestrator",
        help="Leave empty to show all senders",
    )

with filter_col2:
    recipient_filter = st.text_input(
        "Filter by recipient",
        placeholder="e.g., orchestrator, agent-*",
        help="Leave empty to show all recipients",
    )

with filter_col3:
    search_text = st.text_input(
        "Search message content",
        placeholder="Search in messages...",
        help="Search for text in message content",
    )

# Fetch messages
with st.spinner("Loading messages..."):
    messages = get_all_messages(client, selected_mailboxes, limit=int(limit))

if not messages:
    st.info("No messages found in selected mailboxes")
    st.stop()

# Apply filters
filtered_messages = messages

if sender_filter:
    filtered_messages = [
        m for m in filtered_messages
        if sender_filter.lower() in m.get("sender", "").lower()
    ]

if recipient_filter:
    filtered_messages = [
        m for m in filtered_messages
        if recipient_filter.lower() in m.get("recipient", "").lower()
    ]

if search_text:
    filtered_messages = [
        m for m in filtered_messages
        if search_text.lower() in m.get("message", "").lower()
    ]

# Display results
st.divider()
st.subheader(f"Messages ({len(filtered_messages)} found)")

if not filtered_messages:
    st.warning("No messages match the current filters")
    st.stop()

# Display mode selection
display_mode = st.radio(
    "Display mode",
    options=["Table", "Cards", "Detailed"],
    horizontal=True,
    label_visibility="collapsed",
)

if display_mode == "Table":
    # Create dataframe for table view
    table_data = []
    for msg in filtered_messages:
        message_preview = msg.get("message", "")[:100]
        if len(msg.get("message", "")) > 100:
            message_preview += "..."
        
        table_data.append({
            "Timestamp": msg.get("timestamp", ""),
            "Mailbox": msg.get("mailbox", "").split("/")[-1],  # Just the last part
            "Sender": msg.get("sender", ""),
            "Recipient": msg.get("recipient", ""),
            "Message": message_preview,
        })
    
    df = pd.DataFrame(table_data)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )

elif display_mode == "Cards":
    # Card view
    for msg in filtered_messages[:50]:  # Limit to 50 for performance
        render_message_card(msg, show_full=False)
        st.divider()

else:  # Detailed
    # Detailed view with expandable sections
    for i, msg in enumerate(filtered_messages[:50]):  # Limit to 50 for performance
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

# Statistics
with st.sidebar:
    st.header("Statistics")
    
    # Count by sender
    sender_counts = {}
    for msg in filtered_messages:
        sender = msg.get("sender", "unknown")
        sender_counts[sender] = sender_counts.get(sender, 0) + 1
    
    st.subheader("Messages by Sender")
    for sender, count in sorted(sender_counts.items(), key=lambda x: x[1], reverse=True):
        st.metric(sender, count)
    
    st.divider()
    
    # Count by recipient
    recipient_counts = {}
    for msg in filtered_messages:
        recipient = msg.get("recipient", "unknown")
        recipient_counts[recipient] = recipient_counts.get(recipient, 0) + 1
    
    st.subheader("Messages by Recipient")
    for recipient, count in sorted(recipient_counts.items(), key=lambda x: x[1], reverse=True):
        st.metric(recipient, count)
    
    st.divider()
    
    # Mailbox health indicators
    st.subheader("Mailbox Health")
    for mailbox in selected_mailboxes:
        try:
            stream_len = client.xlen(mailbox)
            if stream_len > 0:
                st.success(f"✓ {mailbox.split('/')[-1]}: {stream_len} messages")
            else:
                st.warning(f"○ {mailbox.split('/')[-1]}: Empty")
        except Exception as e:
            st.error(f"✗ {mailbox.split('/')[-1]}: Error - {e}")

# Auto-refresh mechanism
if auto_refresh:
    import time
    time.sleep(2)  # Wait 2 seconds before refreshing
    st.rerun()



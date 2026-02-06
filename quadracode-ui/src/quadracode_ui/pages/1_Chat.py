"""
Chat interface page for Quadracode UI.

This page provides the main chat interface for interacting with the orchestrator.
Supports QUADRACODE_MOCK_MODE for standalone testing with fakeredis.

Uses ``@st.fragment(run_every=10)`` for non-blocking background message polling
instead of a blocking ``time.sleep`` loop.
"""

import uuid
from datetime import UTC, datetime

import redis
import streamlit as st

from quadracode_contracts import HUMAN_CLONE_RECIPIENT, HUMAN_RECIPIENT, SUPERVISOR_RECIPIENT
from quadracode_ui.components.message_list import render_message_input, render_message_list
from quadracode_ui.components.mode_toggle import render_mode_status
from quadracode_ui.config import MOCK_MODE
from quadracode_ui.utils.message_utils import (
    active_supervisor,
    send_message,
    set_supervisor,
    supervisor_mailbox,
)
from quadracode_ui.utils.persistence import (
    load_chat_metadata,
    load_message_history,
    save_chat_metadata,
)
from quadracode_ui.utils.polling_thread import get_polling_thread
from quadracode_ui.utils.redis_client import get_redis_client, test_redis_connection


# Page configuration
st.set_page_config(page_title="Chat - Quadracode", page_icon="💬", layout="wide")

# Get Redis client
client = get_redis_client()
success, error = test_redis_connection(client)

if not success:
    st.error(f"❌ Unable to connect to Redis: {error}")
    st.stop()

# Show mock mode indicator
if MOCK_MODE:
    st.info("🧪 **Mock Mode** - Messages are stored in memory. No external orchestrator connected.")

# ---------------------------------------------------------------------------
# Session state initialisation (persisted via Redis)
# ---------------------------------------------------------------------------
if "chat_loaded" not in st.session_state:
    st.session_state.chat_loaded = False

if not st.session_state.chat_loaded:
    metadata = load_chat_metadata(client)

    if metadata:
        st.session_state.chat_id = metadata.get("chat_id", uuid.uuid4().hex)
        supervisor = metadata.get("supervisor", HUMAN_RECIPIENT)
        set_supervisor(supervisor)

        auto_settings = metadata.get("autonomous_settings")
        if auto_settings:
            st.session_state.autonomous_mode_enabled = True
            st.session_state.autonomous_max_iterations = auto_settings.get("max_iterations", 1000)
            st.session_state.autonomous_max_hours = auto_settings.get("max_hours", 48.0)
            st.session_state.autonomous_max_agents = auto_settings.get("max_agents", 4)
        else:
            st.session_state.autonomous_mode_enabled = False
            st.session_state.autonomous_max_iterations = 1000
            st.session_state.autonomous_max_hours = 48.0
            st.session_state.autonomous_max_agents = 4

        mailbox = supervisor_mailbox()
        st.session_state.history = load_message_history(
            client, mailbox, st.session_state.chat_id, limit=1000,
        )

        try:
            latest = client.xrevrange(mailbox, count=1)
            st.session_state.last_seen_id = latest[0][0] if latest else "0-0"
        except redis.RedisError:
            st.session_state.last_seen_id = "0-0"
    else:
        st.session_state.chat_id = uuid.uuid4().hex
        st.session_state.history = []
        st.session_state.last_seen_id = "0-0"
        st.session_state.autonomous_mode_enabled = False
        st.session_state.autonomous_max_iterations = 1000
        st.session_state.autonomous_max_hours = 48.0
        st.session_state.autonomous_max_agents = 4
        set_supervisor(HUMAN_RECIPIENT)
        save_chat_metadata(client, st.session_state.chat_id, HUMAN_RECIPIENT)

    st.session_state.chat_loaded = True

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("💬 Quadracode Chat")
st.caption("Interact with the orchestrator via Redis Streams")
render_mode_status(compact=True)

# ---------------------------------------------------------------------------
# Sidebar settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Chat Settings")
    st.text_input("Chat ID", value=st.session_state.chat_id, disabled=True)

    st.divider()

    # -- Autonomous mode toggle --
    st.subheader("Autonomous Mode")
    auto_enabled = st.toggle(
        "Enable Autonomous Mode",
        value=st.session_state.autonomous_mode_enabled,
        help="Enable HumanClone autonomous operation",
    )

    if auto_enabled != st.session_state.autonomous_mode_enabled:
        st.session_state.autonomous_mode_enabled = auto_enabled
        supervisor = SUPERVISOR_RECIPIENT if auto_enabled else HUMAN_RECIPIENT
        set_supervisor(supervisor)
        save_chat_metadata(client, st.session_state.chat_id, supervisor)

    if auto_enabled:
        st.session_state.autonomous_max_iterations = st.number_input(
            "Max iterations", min_value=10, max_value=5000,
            value=st.session_state.autonomous_max_iterations, step=10,
        )
        st.session_state.autonomous_max_hours = st.number_input(
            "Max runtime (hours)", min_value=1.0, max_value=168.0,
            value=st.session_state.autonomous_max_hours, step=1.0,
        )
        st.session_state.autonomous_max_agents = st.number_input(
            "Max agents", min_value=1, max_value=20,
            value=st.session_state.autonomous_max_agents, step=1,
        )

        autonomous_settings = {
            "max_iterations": st.session_state.autonomous_max_iterations,
            "max_hours": st.session_state.autonomous_max_hours,
            "max_agents": st.session_state.autonomous_max_agents,
        }
        save_chat_metadata(
            client, st.session_state.chat_id,
            SUPERVISOR_RECIPIENT, autonomous_settings,
        )

    st.divider()

    # -- Danger zone --
    st.subheader("⚠️ Danger Zone")
    if st.button("🗑️ Clear All Context", use_container_width=True, type="secondary"):
        st.session_state.show_clear_confirm = True

    if st.session_state.get("show_clear_confirm", False):
        st.warning(
            "This will delete ALL chat history, workspaces, and Redis data. "
            "This action is IRREVERSIBLE!"
        )
        col1, col2 = st.columns(2)
        if col1.button("✅ Confirm", use_container_width=True, type="primary"):
            from quadracode_ui.utils.persistence import clear_all_context
            from quadracode_ui.utils.workspace_utils import (
                destroy_workspace,
                load_all_workspace_descriptors,
            )

            _ok, message = clear_all_context(client)
            for ws_id in load_all_workspace_descriptors(client):
                destroy_workspace(ws_id, delete_volume=True)

            st.session_state.clear()
            st.session_state.chat_loaded = False
            st.success(f"✅ Context cleared: {message}")
            st.rerun()

        if col2.button("❌ Cancel", use_container_width=True):
            st.session_state.show_clear_confirm = False
            st.rerun()

    st.divider()

    # -- Display settings --
    st.subheader("Display Settings")
    st.session_state.auto_refresh_enabled = st.checkbox(
        "Auto-refresh for new messages",
        value=st.session_state.get("auto_refresh_enabled", True),
        help="Automatically check for new messages every 10 seconds",
    )
    show_payload = st.checkbox("Show message payloads", value=False)

# ---------------------------------------------------------------------------
# Background polling thread
# ---------------------------------------------------------------------------
mailbox = supervisor_mailbox()
polling_thread = get_polling_thread(
    client, mailbox, st.session_state.chat_id, st.session_state.last_seen_id,
)

# ---------------------------------------------------------------------------
# Message display
# ---------------------------------------------------------------------------
if st.session_state.history:
    render_message_list(st.session_state.history, show_payload=show_payload)
else:
    st.info("👋 Start a conversation with the orchestrator!")

# ---------------------------------------------------------------------------
# Message input
# ---------------------------------------------------------------------------
prompt = render_message_input(placeholder="Ask the orchestrator...", key="chat_input")

if prompt:
    autonomous_settings = None
    if st.session_state.autonomous_mode_enabled:
        autonomous_settings = {
            "max_iterations": st.session_state.autonomous_max_iterations,
            "max_hours": st.session_state.autonomous_max_hours,
            "max_agents": st.session_state.autonomous_max_agents,
        }

    mode = "supervisor" if st.session_state.autonomous_mode_enabled else "human"
    ticket_id = send_message(
        client, prompt, st.session_state.chat_id,
        mode=mode, autonomous_settings=autonomous_settings,
    )

    st.session_state.history.append({
        "role": "user",
        "content": prompt,
        "ticket_id": ticket_id,
        "sender": active_supervisor(),
        "timestamp": datetime.now(UTC).isoformat(),
    })
    st.rerun()

# ---------------------------------------------------------------------------
# Non-blocking auto-refresh via st.fragment(run_every=10)
#
# The background PollingThread uses blocking XREAD on Redis to collect new
# messages.  This fragment checks every 10 seconds whether the thread has
# buffered anything and, if so, flushes the buffer into session state and
# triggers a full page rerun.  Unlike the previous ``time.sleep(10)``
# approach this does NOT block the Streamlit event loop.
# ---------------------------------------------------------------------------
@st.fragment(run_every=10)
def _poll_for_new_messages() -> None:
    """Non-blocking periodic check for new orchestrator responses."""
    if not st.session_state.get("auto_refresh_enabled", True):
        return

    if not polling_thread.has_new_messages():
        return

    messages, new_last_id = polling_thread.get_new_messages()
    st.session_state.last_seen_id = new_last_id

    for envelope in messages:
        trace_payload = envelope.payload.get("messages")
        trace_list = trace_payload if isinstance(trace_payload, list) else None
        st.session_state.history.append({
            "role": "assistant",
            "content": envelope.message,
            "ticket_id": envelope.payload.get("ticket_id"),
            "trace": trace_list,
            "sender": envelope.sender,
            "timestamp": envelope.payload.get("timestamp"),
        })

    # Full-page rerun to render the new messages in the main message list.
    st.rerun()


_poll_for_new_messages()

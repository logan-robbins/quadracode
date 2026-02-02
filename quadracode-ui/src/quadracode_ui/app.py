"""
Quadracode UI - Main Entry Point

A Streamlit-based user interface for the Quadracode multi-agent orchestration system.
Provides direct Redis Streams communication with the orchestrator.

Supports QUADRACODE_MOCK_MODE=true for standalone testing:
- Uses fakeredis for in-memory Redis operations
- Mocks agent registry API responses  
- Provides sample data for UI demonstration
"""

import streamlit as st

from quadracode_contracts import HUMAN_CLONE_RECIPIENT, HUMAN_RECIPIENT
from quadracode_ui.components.mode_toggle import render_mode_toggle
from quadracode_ui.config import (
    AGENT_REGISTRY_URL,
    MOCK_MODE,
    REDIS_HOST,
    REDIS_PORT,
)
from quadracode_ui.utils.persistence import save_workspace_descriptor
from quadracode_ui.utils.redis_client import get_redis_client, test_redis_connection
from quadracode_ui.utils.workspace_utils import create_workspace, ensure_default_workspace


# Page configuration must come first
st.set_page_config(
    page_title="Quadracode UI",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Initialize session state defaults
def init_session_state():
    """Initialize all session state variables with defaults."""
    defaults = {
        "supervisor_recipient": HUMAN_RECIPIENT,
        "chat_supervisors": {},
        "workspace_descriptors": {},
        "workspace_messages": [],
        "selected_workspace_id": None,
        "mailbox_monitor_auto_refresh": True,
        "mailbox_monitor_limit": 50,
    }
    
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()

# Test Redis connection
client = get_redis_client()
success, error = test_redis_connection(client)

# Show mock mode banner
if MOCK_MODE:
    st.warning(
        "🧪 **Mock Mode Active** - Running with simulated data. "
        "No external Redis or agent-registry required."
    )
elif success:
    # Ensure default workspace is registered
    # This picks up the 'workspace-default' service from docker-compose
    if not client.exists("qc:workspace:descriptor:default"):
        with st.spinner("Registering default workspace..."):
            ws_success, ws_descriptor, _ = create_workspace("default")
            if ws_success and ws_descriptor:
                save_workspace_descriptor(client, "default", ws_descriptor)

# Header
st.title("🚀 Quadracode UI")
st.markdown("""
**Welcome to Quadracode** - A multi-agent orchestration system with direct Redis Streams communication.

This interface provides real-time interaction with the orchestrator, workspace management, 
and comprehensive system observability.
""")

st.divider()

# Connection status
st.subheader("System Status")

status_col1, status_col2, status_col3 = st.columns(3)

with status_col1:
    if MOCK_MODE:
        st.success("Redis Connected\n\n`fakeredis (mock)`", icon="✅")
    elif success:
        st.success(f"Redis Connected\n\n`{REDIS_HOST}:{REDIS_PORT}`", icon="✅")
    else:
        st.error(f"Redis Disconnected\n\n{error}", icon="❌")

with status_col2:
    if MOCK_MODE:
        st.info("Agent Registry\n\n`mock (simulated)`", icon="🤖")
    elif AGENT_REGISTRY_URL:
        st.info(f"Agent Registry\n\n`{AGENT_REGISTRY_URL}`", icon="🤖")
    else:
        st.warning("Agent Registry\n\nNot Configured", icon="⚠️")

with status_col3:
    mode = st.session_state.get("supervisor_recipient", HUMAN_RECIPIENT)
    if mode == HUMAN_CLONE_RECIPIENT:
        st.info("Mode: HumanClone\n\nAutonomous", icon="🤖")
    else:
        st.info("Mode: Human\n\nDirect Control", icon="👤")

st.divider()

# Navigation guide
st.subheader("📖 Getting Started")

col1, col2 = st.columns(2)

with col1:
    st.markdown("""
    ### 💬 Chat Interface
    
    Interact directly with the Quadracode orchestrator:
    - Send messages and receive responses
    - Toggle between Human and HumanClone modes
    - Enable autonomous operation
    - View conversation history
    
    👉 Navigate to **Chat** in the sidebar
    """)
    
    st.markdown("""
    ### 📁 Workspace Browser
    
    Manage agent workspaces:
    - Create and destroy workspaces
    - Browse files and directories
    - View logs and events
    - Export workspace contents
    
    👉 Navigate to **Workspaces** in the sidebar
    """)

with col2:
    st.markdown("""
    ### 📡 Mailbox Monitor
    
    Monitor all Redis Streams traffic:
    - View messages across all mailboxes
    - Filter by sender/recipient
    - Search message content
    - Real-time auto-refresh
    
    👉 Navigate to **Mailbox Monitor** in the sidebar
    """)
    
    st.markdown("""
    ### 📊 System Dashboard
    
    Observe system-wide metrics:
    - Agent registry status
    - Context engineering metrics
    - Autonomous mode events
    - System health indicators
    
    👉 Navigate to **Dashboard** in the sidebar
    """)

st.divider()

# Mode toggle section
st.subheader("🎛️ Control Mode")

st.markdown("""
Quadracode supports two operational modes:

- **Human Mode**: Direct human supervision with manual message routing
- **HumanClone Mode**: Autonomous operation with HumanClone supervisor
""")

render_mode_toggle()

st.divider()

# Quick links
st.subheader("🔗 Quick Links")

link_col1, link_col2, link_col3, link_col4 = st.columns(4)

with link_col1:
    if st.button("💬 Go to Chat", use_container_width=True, type="primary"):
        st.switch_page("pages/1_💬_Chat.py")

with link_col2:
    if st.button("📡 Mailbox Monitor", use_container_width=True):
        st.switch_page("pages/2_📡_Mailbox_Monitor.py")

with link_col3:
    if st.button("📁 Workspaces", use_container_width=True):
        st.switch_page("pages/3_📁_Workspaces.py")

with link_col4:
    if st.button("📊 Dashboard", use_container_width=True):
        st.switch_page("pages/4_📊_Dashboard.py")

        st.divider()

# System information
with st.expander("ℹ️ System Information", expanded=False):
    if MOCK_MODE:
        st.markdown("""
    **Configuration (Mock Mode):**
    - Redis: `fakeredis (in-memory)`
    - Agent Registry: `mock (simulated data)`
    - Mode: Standalone testing - no external dependencies
    
    **Mock Mode Features:**
    - In-memory Redis using fakeredis
    - Simulated agent registry responses
    - Sample data seeded for demonstration
    - UI fully functional without Docker services
    """)
    else:
        st.markdown(f"""
    **Configuration:**
    - Redis Host: `{REDIS_HOST}`
    - Redis Port: `{REDIS_PORT}`
    - Agent Registry: `{AGENT_REGISTRY_URL or 'Not configured'}`
    
    **Architecture:**
    - Direct Redis Streams communication (no backend API)
    - Real-time message polling and updates
    - Multi-page Streamlit application
    - Workspace isolation via Docker containers
    """)

# Sidebar
with st.sidebar:
    st.header("About Quadracode")
    
    st.markdown("""
    **Quadracode** is a multi-agent orchestration system designed for:
    
    - 🤖 Autonomous agent coordination
    - 📨 Event-driven messaging via Redis Streams
    - 🏗️ Isolated workspace environments
    - 🔄 Progressive refinement protocols
    - 👤 Human-in-the-loop supervision
    """)
    
    st.divider()
    
    st.caption("Version: 0.1.0")
    st.caption("Built with Streamlit + Redis")


if __name__ == "__main__":
    # This is handled by Streamlit's run command
    pass

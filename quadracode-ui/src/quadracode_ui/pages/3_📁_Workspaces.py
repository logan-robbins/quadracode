"""
Workspaces page for Quadracode UI.

This page provides workspace management and file browsing capabilities.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from quadracode_ui.components.file_browser import render_workspace_file_browser
from quadracode_ui.config import WORKSPACE_EXPORT_ROOT
from quadracode_ui.utils.persistence import (
    load_all_workspace_descriptors,
    save_workspace_descriptor,
    delete_workspace_descriptor,
)
from quadracode_ui.utils.redis_client import get_redis_client, test_redis_connection
from quadracode_ui.utils.workspace_utils import (
    copy_from_workspace,
    create_workspace,
    destroy_workspace,
    list_workspace_logs,
    load_workspace_events,
    read_workspace_log,
    summarize_workspace_event,
)


# Page configuration
st.set_page_config(page_title="Workspaces - Quadracode", page_icon="📁", layout="wide")

# Get Redis client
client = get_redis_client()
success, error = test_redis_connection(client)

if not success:
    st.error(f"❌ Unable to connect to Redis: {error}")
    st.stop()

# Header
st.title("📁 Workspace Browser")
st.caption("Inspect workspaces, files, and artifacts created by agents")

# Initialize session state with persistence
if "workspaces_loaded" not in st.session_state:
    st.session_state.workspaces_loaded = False

if not st.session_state.workspaces_loaded:
    # Load workspace descriptors from Redis
    st.session_state.workspace_descriptors = load_all_workspace_descriptors(client)
    st.session_state.workspaces_loaded = True

if "selected_workspace_id" not in st.session_state:
    st.session_state.selected_workspace_id = None
if "workspace_messages" not in st.session_state:
    st.session_state.workspace_messages = []

# Display workspace messages
if st.session_state.workspace_messages:
    for msg in st.session_state.workspace_messages:
        kind = msg.get("kind", "info")
        text = msg.get("message", "")
        if kind == "success":
            st.success(text)
        elif kind == "error":
            st.error(text)
        elif kind == "warning":
            st.warning(text)
        else:
            st.info(text)
    st.session_state.workspace_messages = []

# Sidebar - Workspace list
with st.sidebar:
    st.header("Workspaces")
    
    # Create new workspace
    with st.form("create_workspace"):
        new_ws_id = st.text_input(
            "Workspace ID",
            placeholder="Enter workspace ID",
            help="Unique identifier for the workspace",
        )
        if st.form_submit_button("➕ Create Workspace", use_container_width=True):
            if not new_ws_id.strip():
                st.error("Workspace ID cannot be empty")
            else:
                with st.spinner("Creating workspace..."):
                    success, descriptor, error = create_workspace(new_ws_id.strip())
                    if success and descriptor:
                        # Store in session state
                        st.session_state.workspace_descriptors[new_ws_id.strip()] = descriptor
                        st.session_state.selected_workspace_id = new_ws_id.strip()
                        
                        # Persist to Redis
                        save_workspace_descriptor(client, new_ws_id.strip(), descriptor)
                        
                        st.session_state.workspace_messages.append({
                            "kind": "success",
                            "message": f"✅ Workspace '{new_ws_id}' created successfully",
                        })
                        st.rerun()
                    else:
                        st.error(f"Failed to create workspace: {error}")
    
    st.divider()
    
    # List existing workspaces
    if st.session_state.workspace_descriptors:
        st.subheader("Active Workspaces")
        
        workspace_ids = list(st.session_state.workspace_descriptors.keys())
        
        for ws_id in workspace_ids:
            col1, col2 = st.columns([3, 1])
            
            with col1:
                is_selected = ws_id == st.session_state.selected_workspace_id
                button_type = "primary" if is_selected else "secondary"
                if st.button(
                    f"{'🔵' if is_selected else '⚪'} {ws_id}",
                    key=f"select_{ws_id}",
                    use_container_width=True,
                    type=button_type,
                ):
                    st.session_state.selected_workspace_id = ws_id
                    st.rerun()
            
            with col2:
                if st.button("🗑️", key=f"delete_{ws_id}", help=f"Destroy {ws_id}"):
                    with st.spinner(f"Destroying {ws_id}..."):
                        success, _, error = destroy_workspace(ws_id, delete_volume=True)
                        if success:
                            # Remove from session state
                            st.session_state.workspace_descriptors.pop(ws_id, None)
                            if st.session_state.selected_workspace_id == ws_id:
                                st.session_state.selected_workspace_id = None
                            
                            # Delete from Redis
                            delete_workspace_descriptor(client, ws_id)
                            
                            st.session_state.workspace_messages.append({
                                "kind": "success",
                                "message": f"✅ Workspace '{ws_id}' destroyed",
                            })
                        else:
                            st.session_state.workspace_messages.append({
                                "kind": "error",
                                "message": f"❌ Failed to destroy '{ws_id}': {error}",
                            })
                        st.rerun()
    else:
        st.info("No active workspaces. Create one to get started.")

# Main content area
if not st.session_state.selected_workspace_id:
    st.info("👈 Select or create a workspace to view its contents")
    st.stop()

workspace_id = st.session_state.selected_workspace_id
descriptor = st.session_state.workspace_descriptors.get(workspace_id)

if not descriptor:
    st.error(f"Workspace descriptor not found for: {workspace_id}")
    st.stop()

# Workspace details
st.subheader(f"Workspace: {workspace_id}")

info_col1, info_col2, info_col3 = st.columns(3)
with info_col1:
    st.metric("Container", descriptor.get("container", "unknown"))
with info_col2:
    st.metric("Image", descriptor.get("image", "unknown"))
with info_col3:
    st.metric("Volume", descriptor.get("volume", "unknown"))

with st.expander("Full Descriptor", expanded=False):
    st.json(descriptor)

st.divider()

# Tabs for different views
file_tab, logs_tab, events_tab, export_tab = st.tabs([
    "📄 Files",
    "📋 Logs",
    "📊 Events",
    "💾 Export",
])

with file_tab:
    render_workspace_file_browser(workspace_id)

with logs_tab:
    st.subheader("Workspace Logs")
    st.caption("View log files from `/workspace/logs`")
    
    logs = list_workspace_logs(workspace_id)
    
    if not logs:
        st.info("No logs available yet")
    else:
        selected_log = st.selectbox(
            "Select log file",
            options=logs,
            help="Shows the most recent log files",
        )
        
        if selected_log:
            if st.button("🔄 Refresh Log", key="refresh_log"):
                st.rerun()
            
            success, content = read_workspace_log(workspace_id, selected_log)
            if success:
                st.code(content, language="text")
            else:
                st.error(content)

with events_tab:
    st.subheader("Workspace Events")
    st.caption("Events emitted by workspace operations")
    
    limit = st.slider(
        "Number of events to load",
        min_value=10,
        max_value=200,
        value=50,
        step=10,
        key="events_limit",
    )
    
    if st.button("🔄 Refresh Events", key="refresh_events"):
        st.rerun()
    
    events = load_workspace_events(client, workspace_id, limit=int(limit))
    
    if not events:
        st.info("No workspace events emitted yet")
    else:
        # Event type filter
        event_types = sorted({e.get("event", "unknown") for e in events})
        selected_types = st.multiselect(
            "Filter by event type",
            options=event_types,
            default=event_types,
            key="event_type_filter",
        )
        
        filtered_events = [e for e in events if e.get("event") in selected_types]
        
        if not filtered_events:
            st.warning("No events match the selected types")
        else:
            # Create summary table
            table_data = []
            for event in filtered_events:
                summary = summarize_workspace_event(event.get("payload", {}))
                table_data.append({
                    "Event": event.get("event", "unknown"),
                    "Timestamp": event.get("timestamp", ""),
                    "Summary": summary[:100] + "..." if len(summary) > 100 else summary,
                })
            
            df = pd.DataFrame(table_data)
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            # Detailed view
            with st.expander("Event Details", expanded=False):
                for event in filtered_events:
                    st.markdown(f"**{event.get('timestamp')} — {event.get('event')}**")
                    st.markdown(f"ID: `{event.get('id')}`")
                    st.json(event.get("payload", {}))
                    st.divider()

with export_tab:
    st.subheader("Export Workspace Files")
    st.caption("Copy files from the workspace to your local machine")
    
    with st.form("export_form"):
        source = st.text_input(
            "Source path in workspace",
            value="/workspace/",
            help="Path to file or directory inside the workspace",
        )
        
        default_dest = str((WORKSPACE_EXPORT_ROOT / workspace_id).expanduser())
        destination = st.text_input(
            "Destination path on host",
            value=default_dest,
            help="Where to save the files on your local machine",
        )
        
        if st.form_submit_button("📥 Copy Out", use_container_width=True):
            if not source.strip():
                st.error("Source path cannot be empty")
            else:
                with st.spinner("Copying files..."):
                    dest_path = Path(destination.strip() or default_dest).expanduser()
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    success, result, error = copy_from_workspace(
                        workspace_id,
                        source.strip(),
                        str(dest_path),
                    )
                    
                    if success:
                        bytes_transferred = 0
                        if isinstance(result, dict):
                            copy_result = result.get("workspace_copy", {})
                            bytes_transferred = copy_result.get("bytes_transferred", 0)
                        
                        st.success(
                            f"✅ Successfully copied {source} → {dest_path}\n\n"
                            f"Bytes transferred: {bytes_transferred:,}"
                        )
                    else:
                        st.error(f"❌ Failed to copy files: {error}")
    
    st.divider()
    
    # Quick export presets
    st.subheader("Quick Export Presets")
    
    preset_col1, preset_col2 = st.columns(2)
    
    with preset_col1:
        if st.button("📄 Export all logs", use_container_width=True):
            dest = WORKSPACE_EXPORT_ROOT / workspace_id / "logs"
            dest.mkdir(parents=True, exist_ok=True)
            success, _, error = copy_from_workspace(
                workspace_id,
                "/workspace/logs",
                str(dest),
            )
            if success:
                st.success(f"✅ Logs exported to {dest}")
            else:
                st.error(f"❌ Failed: {error}")
    
    with preset_col2:
        if st.button("📦 Export entire workspace", use_container_width=True):
            dest = WORKSPACE_EXPORT_ROOT / workspace_id
            dest.mkdir(parents=True, exist_ok=True)
            success, _, error = copy_from_workspace(
                workspace_id,
                "/workspace/",
                str(dest),
            )
            if success:
                st.success(f"✅ Workspace exported to {dest}")
            else:
                st.error(f"❌ Failed: {error}")



"""
Workspaces page for Quadracode UI.

This page provides workspace management and file browsing capabilities.
Note: Workspace operations require Docker - limited functionality in mock mode.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from quadracode_ui.components.file_browser import render_workspace_file_browser
from quadracode_ui.config import MOCK_MODE, WORKSPACE_EXPORT_ROOT
from quadracode_ui.utils.persistence import (
    load_all_workspace_descriptors,
    save_workspace_descriptor,
    delete_workspace_descriptor,
)
from quadracode_ui.utils.redis_client import get_redis_client, test_redis_connection
from quadracode_ui.utils.workspace_utils import (
    compare_snapshots,
    copy_from_workspace,
    create_workspace,
    create_workspace_snapshot,
    delete_workspace_snapshot,
    destroy_workspace,
    list_workspace_logs,
    load_workspace_events,
    load_workspace_snapshots,
    read_workspace_log,
    save_workspace_snapshot,
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

# Show mock mode warning for workspaces
if MOCK_MODE:
    st.warning(
        "🧪 **Mock Mode** - Workspace operations require Docker. "
        "Create/destroy operations may fail. Event streams and snapshots work with mock Redis."
    )

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
    
    # Destroy All Workspaces button (Danger Zone)
    if st.session_state.workspace_descriptors:
        st.subheader("⚠️ Danger Zone")
        if st.button("🗑️ Destroy All Workspaces", use_container_width=True, type="secondary"):
            st.session_state.show_destroy_all_confirm = True
        
        # Show confirmation dialog
        if st.session_state.get("show_destroy_all_confirm", False):
            workspace_count = len(st.session_state.workspace_descriptors)
            st.warning(
                f"⚠️ This will destroy ALL {workspace_count} workspace(s) and delete their volumes. "
                "This action is IRREVERSIBLE!"
            )
            col1, col2 = st.columns(2)
            if col1.button("✅ Confirm Destroy All", use_container_width=True, type="primary"):
                # Destroy all workspaces
                workspace_ids = list(st.session_state.workspace_descriptors.keys())
                destroyed_count = 0
                failed_count = 0
                
                for ws_id in workspace_ids:
                    success, _, error = destroy_workspace(ws_id, delete_volume=True)
                    if success:
                        destroyed_count += 1
                        # Delete from Redis
                        delete_workspace_descriptor(client, ws_id)
                    else:
                        failed_count += 1
                        st.session_state.workspace_messages.append({
                            "kind": "error",
                            "message": f"❌ Failed to destroy '{ws_id}': {error}",
                        })
                
                # Clear all from session state
                st.session_state.workspace_descriptors.clear()
                st.session_state.selected_workspace_id = None
                st.session_state.show_destroy_all_confirm = False
                
                # Show summary message
                if destroyed_count > 0:
                    st.session_state.workspace_messages.append({
                        "kind": "success",
                        "message": f"✅ Destroyed {destroyed_count} workspace(s)",
                    })
                if failed_count > 0:
                    st.session_state.workspace_messages.append({
                        "kind": "warning",
                        "message": f"⚠️ {failed_count} workspace(s) failed to destroy",
                    })
                
                st.rerun()
            
            if col2.button("❌ Cancel", use_container_width=True):
                st.session_state.show_destroy_all_confirm = False
                st.rerun()
        
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
file_tab, logs_tab, events_tab, snapshot_tab, export_tab = st.tabs([
    "📄 Files",
    "📋 Logs",
    "📊 Events",
    "📸 Snapshots",
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

with snapshot_tab:
    st.subheader("📸 Workspace Snapshots")
    st.caption("Create point-in-time snapshots and compare changes over time")
    
    # Create snapshot section
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        if st.button("📸 Create New Snapshot", type="primary", use_container_width=True):
            with st.spinner("Creating snapshot..."):
                success, snapshot_data, error = create_workspace_snapshot(workspace_id)
                if success and snapshot_data:
                    # Save to Redis
                    if save_workspace_snapshot(client, workspace_id, snapshot_data):
                        st.success(f"✅ Snapshot created with {snapshot_data['total_files']} files")
                        st.rerun()
                    else:
                        st.error("❌ Failed to save snapshot to Redis")
                else:
                    st.error(f"❌ Failed to create snapshot: {error}")
    
    with col2:
        # Load existing snapshots
        snapshots = load_workspace_snapshots(client, workspace_id)
        if snapshots:
            st.metric("Total Snapshots", len(snapshots))
    
    with col3:
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()
    
    st.divider()
    
    # Display existing snapshots
    if not snapshots:
        st.info("📸 No snapshots yet. Create your first snapshot to track file changes.")
    else:
        # Snapshot selection and comparison
        st.subheader("Snapshot History")
        
        # Display snapshots in a table format
        for idx, snapshot in enumerate(snapshots):
            with st.expander(
                f"Snapshot {idx + 1}: {snapshot['timestamp'][:19]} "
                f"({snapshot['total_files']} files, {snapshot['total_size']:,} bytes)",
                expanded=(idx == 0)
            ):
                # Snapshot metadata
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Files", snapshot['total_files'])
                with col2:
                    st.metric("Total Size", f"{snapshot['total_size']:,} bytes")
                with col3:
                    st.caption(f"Created: {snapshot['timestamp']}")
                with col4:
                    if st.button("🗑️ Delete", key=f"delete_snap_{snapshot['snapshot_id']}"):
                        if delete_workspace_snapshot(client, workspace_id, snapshot['snapshot_id']):
                            st.success("Snapshot deleted")
                            st.rerun()
                        else:
                            st.error("Failed to delete snapshot")
                
                # Show file list
                if st.checkbox("Show files", key=f"show_files_{snapshot['snapshot_id']}"):
                    files_data = []
                    for file_path, file_info in snapshot['files'].items():
                        files_data.append({
                            "File": file_path,
                            "Size": f"{file_info['size']:,}" if file_info['size'] else "0",
                            "Modified": file_info.get('modified', 'N/A'),
                            "Checksum": file_info['checksum'][:12] + "...",
                        })
                    
                    if files_data:
                        df = pd.DataFrame(files_data)
                        st.dataframe(df, use_container_width=True, hide_index=True)
                
                # Compare with current state button
                if st.button(
                    "🔍 Compare with Current", 
                    key=f"compare_current_{snapshot['snapshot_id']}",
                    use_container_width=True
                ):
                    st.session_state[f"compare_mode_{workspace_id}"] = "current"
                    st.session_state[f"compare_snapshot_{workspace_id}"] = snapshot
                    st.rerun()
        
        # Comparison view
        if st.session_state.get(f"compare_mode_{workspace_id}"):
            st.divider()
            st.subheader("📊 Comparison Results")
            
            compare_snapshot = st.session_state.get(f"compare_snapshot_{workspace_id}")
            if compare_snapshot:
                # Perform comparison
                with st.spinner("Comparing..."):
                    if st.session_state[f"compare_mode_{workspace_id}"] == "current":
                        # Compare snapshot with current state
                        comparison = compare_snapshots(workspace_id, compare_snapshot, None)
                    else:
                        # Future: compare two snapshots
                        comparison = {"error": "Two-snapshot comparison not yet implemented"}
                
                if "error" in comparison:
                    st.error(comparison["error"])
                else:
                    # Display comparison summary
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("➕ Added", comparison['summary']['added_count'], 
                                help="Files added since snapshot")
                    with col2:
                        st.metric("➖ Deleted", comparison['summary']['deleted_count'],
                                help="Files deleted since snapshot")
                    with col3:
                        st.metric("✏️ Modified", comparison['summary']['modified_count'],
                                help="Files modified since snapshot")
                    with col4:
                        st.metric("✓ Unchanged", comparison['summary']['unchanged_count'],
                                help="Files unchanged since snapshot")
                    
                    # Detailed changes
                    if comparison['summary']['added_count'] > 0:
                        with st.expander(f"➕ Added Files ({comparison['summary']['added_count']})", expanded=True):
                            for file in comparison['added']:
                                st.text(f"  + {file}")
                    
                    if comparison['summary']['deleted_count'] > 0:
                        with st.expander(f"➖ Deleted Files ({comparison['summary']['deleted_count']})", expanded=True):
                            for file in comparison['deleted']:
                                st.text(f"  - {file}")
                    
                    if comparison['summary']['modified_count'] > 0:
                        with st.expander(f"✏️ Modified Files ({comparison['summary']['modified_count']})", expanded=True):
                            for file in comparison['modified']:
                                st.text(f"  ~ {file}")
                    
                    # Clear comparison button
                    if st.button("❌ Clear Comparison", type="secondary"):
                        del st.session_state[f"compare_mode_{workspace_id}"]
                        del st.session_state[f"compare_snapshot_{workspace_id}"]
                        st.rerun()

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



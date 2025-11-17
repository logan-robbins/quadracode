"""
System Dashboard page for Quadracode UI.

This page provides system-wide metrics, agent registry status, and observability.
"""

import json
from collections import Counter

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from quadracode_ui.config import (
    AGENT_REGISTRY_URL,
    AUTONOMOUS_EVENTS_LIMIT,
    CONTEXT_METRICS_LIMIT,
    CONTEXT_METRICS_STREAM,
    AUTONOMOUS_EVENTS_STREAM,
    UI_BARE,
)
from quadracode_ui.utils.redis_client import get_redis_client, test_redis_connection


# Page configuration
st.set_page_config(page_title="Dashboard - Quadracode", page_icon="📊", layout="wide")

# Get Redis client
client = get_redis_client()
success, error = test_redis_connection(client)

if not success:
    st.error(f"❌ Unable to connect to Redis: {error}")
    st.stop()

# Header
st.title("📊 System Dashboard")
st.caption("Overview of system health, metrics, and agent activity")


# Helper functions
@st.cache_data(ttl=5.0, show_spinner=False)
def fetch_agent_registry_data():
    """Fetches agent registry data."""
    if UI_BARE or not AGENT_REGISTRY_URL:
        return {"agents": [], "stats": None, "error": "Registry not configured"}
    
    base = AGENT_REGISTRY_URL.rstrip("/")
    result = {"agents": [], "stats": None, "error": None}
    
    try:
        agents_resp = httpx.get(f"{base}/agents", timeout=2.0)
        agents_resp.raise_for_status()
        result["agents"] = agents_resp.json().get("agents", [])
    except Exception as exc:
        result["error"] = f"Failed to load agents: {exc}"
        return result
    
    try:
        stats_resp = httpx.get(f"{base}/stats", timeout=2.0)
        stats_resp.raise_for_status()
        result["stats"] = stats_resp.json()
    except Exception as exc:
        result["error"] = f"Failed to load stats: {exc}"
    
    return result


def load_context_metrics(limit: int = 100):
    """Loads context metrics from Redis."""
    try:
        raw_entries = client.xrevrange(CONTEXT_METRICS_STREAM, count=limit)
    except Exception:
        return []
    
    parsed = []
    for entry_id, fields in raw_entries:
        try:
            payload = json.loads(fields.get("payload", "{}"))
        except json.JSONDecodeError:
            payload = {}
        
        parsed.append({
            "id": entry_id,
            "event": fields.get("event", "unknown"),
            "timestamp": fields.get("timestamp", ""),
            "payload": payload,
        })
    
    return list(reversed(parsed))


def load_autonomous_events(limit: int = 100):
    """Loads autonomous events from Redis."""
    try:
        raw_entries = client.xrevrange(AUTONOMOUS_EVENTS_STREAM, count=limit)
    except Exception:
        return []
    
    parsed = []
    for entry_id, fields in raw_entries:
        try:
            payload = json.loads(fields.get("payload", "{}"))
        except json.JSONDecodeError:
            payload = {}
        
        parsed.append({
            "id": entry_id,
            "event": fields.get("event", "unknown"),
            "timestamp": fields.get("timestamp", ""),
            "payload": payload,
        })
    
    return list(reversed(parsed))


# Tabs
overview_tab, agents_tab, metrics_tab, autonomous_tab = st.tabs([
    "🏠 Overview",
    "🤖 Agents",
    "📈 Context Metrics",
    "🔄 Autonomous",
])

with overview_tab:
    st.subheader("System Overview")
    
    # Fetch agent data
    registry_data = fetch_agent_registry_data()
    
    # Metrics cards
    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    
    with metric_col1:
        total_agents = len(registry_data.get("agents", []))
        st.metric("Total Agents", total_agents)
    
    with metric_col2:
        stats = registry_data.get("stats") or {}
        healthy = stats.get("healthy_agents", 0)
        st.metric("Healthy Agents", healthy)
    
    with metric_col3:
        # Count workspace descriptors from session state
        workspace_count = len(st.session_state.get("workspace_descriptors", {}))
        st.metric("Active Workspaces", workspace_count)
    
    with metric_col4:
        # Redis connection status
        redis_status = "✅ Connected" if success else "❌ Disconnected"
        st.metric("Redis Status", redis_status)
    
    st.divider()
    
    # Agent Registry Status
    st.subheader("Agent Registry")
    
    if registry_data.get("error"):
        st.warning(registry_data["error"])
    else:
        agents = registry_data.get("agents", [])
        if agents:
            # Create agents table
            agent_data = []
            for agent in agents:
                agent_data.append({
                    "Agent ID": agent.get("id", ""),
                    "Status": agent.get("status", ""),
                    "Type": agent.get("type", ""),
                    "Last Heartbeat": agent.get("last_heartbeat", ""),
                })
            
            df = pd.DataFrame(agent_data)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No agents registered")

with agents_tab:
    st.subheader("Agent Registry Details")
    
    registry_data = fetch_agent_registry_data()
    
    if registry_data.get("error"):
        st.error(registry_data["error"])
    else:
        agents = registry_data.get("agents", [])
        
        if not agents:
            st.info("No agents registered in the system")
        else:
            # Agent status distribution
            status_col, type_col = st.columns(2)
            
            with status_col:
                st.markdown("**Agent Status Distribution**")
                status_counts = Counter(agent.get("status", "unknown") for agent in agents)
                status_data = pd.DataFrame([
                    {"Status": k, "Count": v} for k, v in status_counts.items()
                ])
                fig = px.pie(status_data, names="Status", values="Count")
                st.plotly_chart(fig, use_container_width=True)
            
            with type_col:
                st.markdown("**Agent Type Distribution**")
                type_counts = Counter(agent.get("type", "unknown") for agent in agents)
                type_data = pd.DataFrame([
                    {"Type": k, "Count": v} for k, v in type_counts.items()
                ])
                fig = px.bar(type_data, x="Type", y="Count")
                st.plotly_chart(fig, use_container_width=True)
            
            st.divider()
            
            # Detailed agent list
            st.subheader("Agent Details")
            for agent in agents:
                with st.expander(f"🤖 {agent.get('id', 'unknown')}", expanded=False):
                    st.json(agent)

with metrics_tab:
    st.subheader("Context Engineering Metrics")
    st.caption("Metrics emitted by the context engineering node")
    
    limit = st.slider(
        "History depth",
        min_value=50,
        max_value=CONTEXT_METRICS_LIMIT,
        value=min(150, CONTEXT_METRICS_LIMIT),
        key="metrics_limit",
    )
    
    if st.button("🔄 Refresh Metrics", key="refresh_metrics"):
        st.cache_data.clear()
        st.rerun()
    
    metrics = load_context_metrics(limit=int(limit))
    
    if not metrics:
        st.info("No context metrics emitted yet")
    else:
        # Latest metrics summary
        latest = metrics[-1]
        latest_payload = latest.get("payload", {})
        
        summary_col1, summary_col2, summary_col3 = st.columns(3)
        
        with summary_col1:
            quality = latest_payload.get("quality_score")
            quality_str = f"{quality:.2f}" if isinstance(quality, (int, float)) else "n/a"
            st.metric("Latest Quality Score", quality_str)
        
        with summary_col2:
            focus = latest_payload.get("focus_metric") or "—"
            st.metric("Latest Focus Metric", focus)
        
        with summary_col3:
            window = latest_payload.get("context_window_used", 0)
            st.metric("Latest Context Tokens", window)
        
        st.divider()
        
        # Quality trend chart
        quality_data = []
        for entry in metrics:
            quality = entry["payload"].get("quality_score")
            if quality is not None:
                quality_data.append({
                    "timestamp": entry["timestamp"],
                    "quality": quality,
                })
        
        if quality_data:
            st.subheader("Quality Score Trend")
            df = pd.DataFrame(quality_data)
            fig = px.line(df, x="timestamp", y="quality", title="Quality Score Over Time")
            st.plotly_chart(fig, use_container_width=True)
        
        # Context window usage
        window_data = []
        for entry in metrics:
            tokens = entry["payload"].get("context_window_used")
            if tokens is not None:
                window_data.append({
                    "timestamp": entry["timestamp"],
                    "tokens": tokens,
                })
        
        if window_data:
            st.subheader("Context Window Usage")
            df = pd.DataFrame(window_data)
            fig = px.area(df, x="timestamp", y="tokens", title="Context Tokens Over Time")
            st.plotly_chart(fig, use_container_width=True)
        
        # Operation distribution
        operations = Counter(
            entry["payload"].get("operation")
            for entry in metrics
            if entry.get("event") == "tool_response" and entry["payload"].get("operation")
        )
        
        if operations:
            st.subheader("Operation Distribution")
            op_data = pd.DataFrame([
                {"Operation": k, "Count": v}
                for k, v in sorted(operations.items(), key=lambda x: x[1], reverse=True)
            ])
            fig = px.bar(op_data, x="Operation", y="Count", title="Tool Operations")
            st.plotly_chart(fig, use_container_width=True)
        
        # Raw metrics
        with st.expander("Raw Metrics Data", expanded=False):
            st.json(metrics[-50:])

with autonomous_tab:
    st.subheader("Autonomous Mode Events")
    st.caption("Checkpoints, critiques, and escalations during autonomous operations")
    
    limit = st.slider(
        "Event history depth",
        min_value=50,
        max_value=AUTONOMOUS_EVENTS_LIMIT,
        value=min(150, AUTONOMOUS_EVENTS_LIMIT),
        key="autonomous_limit",
    )
    
    if st.button("🔄 Refresh Events", key="refresh_autonomous"):
        st.cache_data.clear()
        st.rerun()
    
    events = load_autonomous_events(limit=int(limit))
    
    if not events:
        st.info("No autonomous events recorded yet")
    else:
        # Latest event
        latest = events[-1]
        st.subheader("Latest Event")
        
        event_col1, event_col2, event_col3 = st.columns(3)
        
        with event_col1:
            st.metric("Type", latest.get("event", "unknown"))
        
        with event_col2:
            st.metric("Timestamp", latest.get("timestamp", "")[:19])
        
        with event_col3:
            payload = latest.get("payload", {})
            thread_id = payload.get("thread_id") if isinstance(payload, dict) else None
            st.metric("Thread", thread_id or "—")
        
        st.divider()
        
        # Event type distribution
        event_counts = Counter(e.get("event", "unknown") for e in events)
        st.subheader("Event Counts")
        count_data = pd.DataFrame([
            {"Event Type": k, "Count": v}
            for k, v in sorted(event_counts.items())
        ])
        st.dataframe(count_data, use_container_width=True, hide_index=True)
        
        # Event timeline
        st.subheader("Event Timeline")
        timeline_data = []
        for event in events:
            timeline_data.append({
                "Timestamp": event.get("timestamp", ""),
                "Event": event.get("event", "unknown"),
            })
        
        df = pd.DataFrame(timeline_data)
        fig = px.scatter(
            df,
            x="Timestamp",
            y="Event",
            title="Event Timeline",
            height=400,
        )
        st.plotly_chart(fig, use_container_width=True)
        
        # Raw events
        with st.expander("Raw Events Data", expanded=False):
            st.json(events[-50:])

# Sidebar - Refresh controls
with st.sidebar:
    st.header("Dashboard Controls")
    
    if st.button("🔄 Refresh All Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    
    st.subheader("Auto-refresh")
    auto_refresh = st.checkbox("Enable auto-refresh", value=False)
    
    if auto_refresh:
        refresh_interval = st.slider(
            "Refresh interval (seconds)",
            min_value=5,
            max_value=60,
            value=10,
            step=5,
        )
        st.caption(f"Refreshing every {refresh_interval} seconds")
        
        import time
        time.sleep(refresh_interval)
        st.rerun()



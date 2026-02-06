"""
System Dashboard page for Quadracode UI.

This page provides system-wide metrics, agent registry status, and observability.
Supports QUADRACODE_MOCK_MODE for standalone testing with mock data.

Uses ``@st.fragment(run_every=…)`` for non-blocking auto-refresh of the
dashboard tabs instead of a blocking ``time.sleep`` loop.
"""

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from quadracode_ui.config import (
    AGENT_REGISTRY_URL,
    AUTONOMOUS_EVENTS_LIMIT,
    AUTONOMOUS_EVENTS_STREAM,
    CONTEXT_METRICS_LIMIT,
    CONTEXT_METRICS_STREAM,
    MOCK_MODE,
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

if MOCK_MODE:
    st.info("🧪 **Mock Mode Active** - Using simulated data for demonstration")

# Header
st.title("📊 System Dashboard")
st.caption("Overview of system health, metrics, and agent activity")


# ---------------------------------------------------------------------------
# Data-loading helpers
# ---------------------------------------------------------------------------

def _get_mock_agent_registry_data() -> dict[str, Any]:
    """Returns mock agent registry data for demonstration."""
    now = datetime.now(UTC).isoformat()
    return {
        "agents": [
            {
                "agent_id": "orchestrator", "status": "healthy", "type": "orchestrator",
                "last_heartbeat": now, "hotpath": False, "port": 8123,
                "capabilities": ["coordination", "task_dispatch"],
            },
            {
                "agent_id": "agent-a1b2c3", "status": "healthy", "type": "worker",
                "last_heartbeat": now, "hotpath": True, "port": 8124,
                "capabilities": ["code_execution", "file_operations"],
            },
            {
                "agent_id": "agent-d4e5f6", "status": "idle", "type": "worker",
                "last_heartbeat": now, "hotpath": False, "port": 8125,
                "capabilities": ["research", "summarization"],
            },
        ],
        "stats": {
            "total_agents": 3, "healthy_agents": 2,
            "idle_agents": 1, "unhealthy_agents": 0,
        },
        "error": None,
    }


@st.cache_data(ttl=5.0, show_spinner=False)
def fetch_agent_registry_data() -> dict[str, Any]:
    """Fetches agent registry data from the agent registry API."""
    if MOCK_MODE:
        return _get_mock_agent_registry_data()

    if UI_BARE or not AGENT_REGISTRY_URL:
        return {"agents": [], "stats": None, "error": "Registry not configured"}

    base = AGENT_REGISTRY_URL.rstrip("/")
    result: dict[str, Any] = {"agents": [], "stats": None, "error": None}

    try:
        agents_resp = httpx.get(f"{base}/agents", timeout=2.0)
        agents_resp.raise_for_status()
        result["agents"] = agents_resp.json().get("agents", [])
    except httpx.HTTPError as exc:
        result["error"] = f"Failed to load agents: {exc}"
        return result

    try:
        stats_resp = httpx.get(f"{base}/stats", timeout=2.0)
        stats_resp.raise_for_status()
        result["stats"] = stats_resp.json()
    except httpx.HTTPError as exc:
        result["error"] = f"Failed to load stats: {exc}"

    return result


def load_context_metrics(limit: int = 100) -> list[dict[str, Any]]:
    """Loads context metrics from the ``qc:context:metrics`` Redis stream."""
    try:
        raw_entries = client.xrevrange(CONTEXT_METRICS_STREAM, count=limit)
    except Exception:  # noqa: BLE001
        return []

    parsed: list[dict[str, Any]] = []
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


def load_autonomous_events(limit: int = 100) -> list[dict[str, Any]]:
    """Loads autonomous events from the ``qc:autonomous:events`` Redis stream."""
    try:
        raw_entries = client.xrevrange(AUTONOMOUS_EVENTS_STREAM, count=limit)
    except Exception:  # noqa: BLE001
        return []

    parsed: list[dict[str, Any]] = []
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


# ---------------------------------------------------------------------------
# Sidebar controls (outside fragment so changes trigger full reruns)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Dashboard Controls")

    if st.button("🔄 Refresh All Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    st.subheader("Auto-refresh")
    auto_refresh = st.checkbox("Enable auto-refresh", value=False)
    refresh_interval: int = 10

    if auto_refresh:
        refresh_interval = st.slider(
            "Refresh interval (seconds)",
            min_value=5, max_value=60, value=10, step=5,
        )
        st.caption(f"Refreshing every {refresh_interval} seconds")

# ---------------------------------------------------------------------------
# Dashboard tabs wrapped in a fragment for non-blocking auto-refresh.
# When auto-refresh is enabled the fragment auto-reruns every
# ``refresh_interval`` seconds, re-fetching all data from Redis /
# agent-registry and redrawing the charts.
# ---------------------------------------------------------------------------
_auto_interval = refresh_interval if auto_refresh else None


@st.fragment(run_every=_auto_interval)
def _render_dashboard() -> None:  # noqa: C901 – page-level display function
    """Render all dashboard tabs (overview, agents, metrics, autonomous)."""

    overview_tab, agents_tab, metrics_tab, autonomous_tab = st.tabs([
        "🏠 Overview", "🤖 Agents", "📈 Context Metrics", "🔄 Autonomous",
    ])

    # ---- Overview ----
    with overview_tab:
        st.subheader("System Overview")
        registry_data = fetch_agent_registry_data()

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Total Agents", len(registry_data.get("agents", [])))
        with m2:
            stats = registry_data.get("stats") or {}
            st.metric("Healthy Agents", stats.get("healthy_agents", 0))
        with m3:
            st.metric("Active Workspaces", len(st.session_state.get("workspace_descriptors", {})))
        with m4:
            st.metric("Redis Status", "✅ Connected" if success else "❌ Disconnected")

        st.divider()
        st.subheader("Agent Registry")

        if registry_data.get("error"):
            st.warning(registry_data["error"])
        else:
            agents = registry_data.get("agents", [])
            if agents:
                df = pd.DataFrame([
                    {
                        "Agent ID": a.get("agent_id", ""),
                        "Status": a.get("status", ""),
                        "Type": a.get("type", ""),
                        "Last Heartbeat": a.get("last_heartbeat", ""),
                    }
                    for a in agents
                ])
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No agents registered")

    # ---- Agents ----
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
                status_col, type_col = st.columns(2)
                with status_col:
                    st.markdown("**Agent Status Distribution**")
                    sc = Counter(a.get("status", "unknown") for a in agents)
                    fig = px.pie(
                        pd.DataFrame([{"Status": k, "Count": v} for k, v in sc.items()]),
                        names="Status", values="Count", title="Agent Status",
                        hole=0.3, color_discrete_sequence=px.colors.qualitative.Set3,
                    )
                    fig.update_traces(textposition="inside", textinfo="percent+label")
                    st.plotly_chart(fig, use_container_width=True)

                with type_col:
                    st.markdown("**Agent Type Distribution**")
                    tc = Counter(a.get("type", "unknown") for a in agents)
                    fig = px.bar(
                        pd.DataFrame([{"Type": k, "Count": v} for k, v in tc.items()]),
                        x="Type", y="Count", title="Agent Types", color="Type",
                        color_discrete_sequence=px.colors.qualitative.Pastel,
                    )
                    fig.update_layout(showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)

                st.divider()
                st.subheader("Agent Activity Drill-Down")

                agent_ids = [a.get("agent_id", "unknown") for a in agents]
                selected_agent = st.selectbox(
                    "Select agent to view details", options=agent_ids, key="agent_selector",
                )

                if selected_agent:
                    agent_info = next((a for a in agents if a.get("agent_id") == selected_agent), None)
                    if agent_info:
                        ac1, ac2, ac3, ac4 = st.columns(4)
                        with ac1:
                            st.metric("Status", agent_info.get("status", "unknown"))
                        with ac2:
                            st.metric("Type", agent_info.get("type", "unknown"))
                        with ac3:
                            last_hb = agent_info.get("last_heartbeat", "")
                            hb_display = "never"
                            if last_hb:
                                try:
                                    hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                                    secs = (datetime.now(UTC) - hb_time).total_seconds()
                                    if secs < 60:
                                        hb_display = f"{int(secs)}s ago"
                                    elif secs < 3600:
                                        hb_display = f"{int(secs / 60)}m ago"
                                    else:
                                        hb_display = f"{int(secs / 3600)}h ago"
                                except (ValueError, AttributeError):
                                    hb_display = "unknown"
                            st.metric("Last Heartbeat", hb_display)
                        with ac4:
                            st.metric("Hotpath", "✅ Yes" if agent_info.get("hotpath") else "❌ No")

                        st.divider()
                        cc1, cc2 = st.columns(2)
                        with cc1:
                            st.markdown("**Agent Configuration**")
                            extra = {
                                k: v for k, v in agent_info.items()
                                if k not in {"agent_id", "status", "type", "last_heartbeat", "hotpath"}
                            }
                            st.json(extra) if extra else st.caption("No additional configuration")
                        with cc2:
                            st.markdown("**Full Agent Data**")
                            st.json(agent_info)

                st.divider()
                st.subheader("All Agents Summary")
                for agent in agents:
                    with st.expander(f"🤖 {agent.get('agent_id', 'unknown')}", expanded=False):
                        st.json(agent)

    # ---- Context Metrics ----
    with metrics_tab:
        st.subheader("Context Engineering Metrics")
        st.caption("Metrics emitted by the context engineering node")

        depth = st.slider(
            "History depth", min_value=50, max_value=CONTEXT_METRICS_LIMIT,
            value=min(150, CONTEXT_METRICS_LIMIT), key="metrics_limit",
        )

        if st.button("🔄 Refresh Metrics", key="refresh_metrics"):
            st.cache_data.clear()
            st.rerun()

        metrics = load_context_metrics(limit=int(depth))

        if not metrics:
            st.info("No context metrics emitted yet")
        else:
            latest_payload = metrics[-1].get("payload", {})
            sc1, sc2, sc3 = st.columns(3)
            with sc1:
                q = latest_payload.get("quality_score")
                st.metric("Latest Quality Score", f"{q:.2f}" if isinstance(q, (int, float)) else "n/a")
            with sc2:
                st.metric("Latest Focus Metric", latest_payload.get("focus_metric") or "—")
            with sc3:
                st.metric("Latest Context Tokens", latest_payload.get("context_window_used", 0))

            st.divider()

            quality_data = [
                {"timestamp": e["timestamp"], "quality": e["payload"]["quality_score"]}
                for e in metrics if e["payload"].get("quality_score") is not None
            ]
            if quality_data:
                st.subheader("Quality Score Trend")
                fig = px.line(pd.DataFrame(quality_data), x="timestamp", y="quality",
                              title="Quality Score Over Time", markers=True)
                fig.update_layout(hovermode="x unified", xaxis_title="Time", yaxis_title="Quality Score")
                fig.update_traces(line_color="#00cc96", line_width=2, marker={"size": 6})
                st.plotly_chart(fig, use_container_width=True, key="quality_chart")

            window_data = [
                {"timestamp": e["timestamp"], "tokens": e["payload"]["context_window_used"]}
                for e in metrics if e["payload"].get("context_window_used") is not None
            ]
            if window_data:
                st.subheader("Context Window Usage")
                fig = px.area(pd.DataFrame(window_data), x="timestamp", y="tokens",
                              title="Context Tokens Over Time")
                fig.update_layout(hovermode="x unified", xaxis_title="Time", yaxis_title="Tokens Used")
                fig.update_traces(fillcolor="rgba(99, 110, 250, 0.3)", line_color="#636efa", line_width=2)
                st.plotly_chart(fig, use_container_width=True, key="context_chart")

            operations = Counter(
                e["payload"].get("operation")
                for e in metrics
                if e.get("event") == "tool_response" and e["payload"].get("operation")
            )
            if operations:
                st.subheader("Operation Distribution")
                fig = px.bar(
                    pd.DataFrame([{"Operation": k, "Count": v}
                                  for k, v in sorted(operations.items(), key=lambda x: x[1], reverse=True)]),
                    x="Operation", y="Count", title="Tool Operations",
                )
                st.plotly_chart(fig, use_container_width=True)

            with st.expander("Raw Metrics Data", expanded=False):
                st.json(metrics[-50:])

    # ---- Autonomous ----
    with autonomous_tab:
        st.subheader("Autonomous Mode Events")
        st.caption("Checkpoints, critiques, and escalations during autonomous operations")

        edepth = st.slider(
            "Event history depth", min_value=50, max_value=AUTONOMOUS_EVENTS_LIMIT,
            value=min(150, AUTONOMOUS_EVENTS_LIMIT), key="autonomous_limit",
        )

        if st.button("🔄 Refresh Events", key="refresh_autonomous"):
            st.cache_data.clear()
            st.rerun()

        events = load_autonomous_events(limit=int(edepth))

        if not events:
            st.info("No autonomous events recorded yet")
        else:
            latest = events[-1]
            st.subheader("Latest Event")
            ec1, ec2, ec3 = st.columns(3)
            with ec1:
                st.metric("Type", latest.get("event", "unknown"))
            with ec2:
                st.metric("Timestamp", latest.get("timestamp", "")[:19])
            with ec3:
                p = latest.get("payload", {})
                st.metric("Thread", (p.get("thread_id") if isinstance(p, dict) else None) or "—")

            st.divider()

            event_counts = Counter(e.get("event", "unknown") for e in events)
            st.subheader("Event Counts")
            st.dataframe(
                pd.DataFrame([{"Event Type": k, "Count": v} for k, v in sorted(event_counts.items())]),
                use_container_width=True, hide_index=True,
            )

            st.subheader("Event Timeline")
            event_colors = {
                "checkpoint": "#00cc96", "critique": "#ef553b",
                "escalation": "#ffa15a", "prp_transition": "#ab63fa",
                "approval": "#19d3f3", "rejection": "#ff6692",
            }
            tl = pd.DataFrame([
                {
                    "Timestamp": e.get("timestamp", ""),
                    "Event": e.get("event", "unknown"),
                    "Color": event_colors.get(e.get("event", ""), "#636efa"),
                }
                for e in events
            ])

            fig = go.Figure()
            for et in tl["Event"].unique():
                edf = tl[tl["Event"] == et]
                fig.add_trace(go.Scatter(
                    x=edf["Timestamp"], y=edf["Event"], mode="markers", name=et,
                    marker={"size": 12, "color": edf["Color"].iloc[0] if not edf.empty else "#636efa",
                            "line": {"width": 1, "color": "white"}},
                    text=et, hovertemplate="<b>%{y}</b><br>%{x}<extra></extra>",
                ))
            fig.update_layout(
                title="Autonomous Event Timeline", height=400,
                hovermode="closest", showlegend=True,
                xaxis_title="Time", yaxis_title="Event Type",
            )
            st.plotly_chart(fig, use_container_width=True, key="timeline_chart")

            with st.expander("Raw Events Data", expanded=False):
                st.json(events[-50:])


_render_dashboard()

"""
This module builds the primary LangGraph agent graph.

It initializes and configures the agent's core operational workflow by leveraging 
the shared runtime's graph-building utilities. The agent's specific behavior is 
defined by the PROFILE, which includes the system prompt, tools, and other 
configurations. The resulting `graph` object is a runnable LangGraph instance 
that executes the agent's logic.
"""
from __future__ import annotations

from quadracode_runtime.graph import build_graph
from quadracode_agent.profile import PROFILE


graph = build_graph(PROFILE.system_prompt)

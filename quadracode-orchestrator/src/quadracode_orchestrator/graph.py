"""
This module constructs the primary LangGraph for the Quadracode orchestrator.

It leverages the `build_graph` utility from the shared `quadracode_runtime` to 
create a runnable graph instance. The orchestrator's specific behavior is 
defined by its `PROFILE`, which includes a specialized system prompt that guides 
its high-level decision-making and coordination tasks. The resulting `graph` 
object is a fully configured LangGraph that executes the orchestrator's core 
logic.
"""
from __future__ import annotations

from quadracode_runtime.graph import build_graph
from quadracode_orchestrator.profile import PROFILE


graph = build_graph(PROFILE.system_prompt)

"""LangGraph graph construction for the Quadracode orchestrator.

Delegates to ``build_graph`` from ``quadracode_runtime``, parameterised with the
orchestrator's system prompt (which varies based on autonomous mode). The resulting
``graph`` is a fully compiled LangGraph instance ready for the runtime runner.
"""
from __future__ import annotations

from quadracode_runtime.graph import build_graph

from .profile import PROFILE

graph = build_graph(PROFILE.system_prompt)

from __future__ import annotations

from quadracode_runtime.graph import build_graph
from quadracode_agent.profile import PROFILE


graph = build_graph(PROFILE.system_prompt)

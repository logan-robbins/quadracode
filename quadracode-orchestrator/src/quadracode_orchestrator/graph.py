from __future__ import annotations

from quadracode_runtime.graph import build_graph
from quadracode_orchestrator.profile import PROFILE


graph = build_graph(PROFILE.system_prompt)

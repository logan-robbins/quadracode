"""
This module defines the runtime profile for the Quadracode orchestrator.

It dynamically selects the appropriate system prompt based on whether autonomous 
mode is enabled, and then customizes the base "orchestrator" profile from the 
shared `quadracode_runtime`. This module is the central point of configuration for 
the orchestrator's identity, tools, and behavioral instructions. The resulting 
`PROFILE` object is used to initialize the orchestrator's runtime environment 
and graph.
"""
from __future__ import annotations

from dataclasses import replace

from quadracode_runtime.profiles import is_autonomous_mode_enabled, load_profile

from .prompts.system import SYSTEM_PROMPT
from .prompts.autonomous import AUTONOMOUS_SYSTEM_PROMPT

_PROMPT = AUTONOMOUS_SYSTEM_PROMPT if is_autonomous_mode_enabled() else SYSTEM_PROMPT
PROFILE = replace(load_profile("orchestrator"), system_prompt=_PROMPT)

from __future__ import annotations

from dataclasses import replace

from quadracode_runtime import AGENT_PROFILE

from .prompts.system import SYSTEM_PROMPT

PROFILE = replace(AGENT_PROFILE, system_prompt=SYSTEM_PROMPT)

from __future__ import annotations

from dataclasses import replace

from quadracode_runtime import ORCHESTRATOR_PROFILE

from .prompts.system import SYSTEM_PROMPT

PROFILE = replace(ORCHESTRATOR_PROFILE, system_prompt=SYSTEM_PROMPT)

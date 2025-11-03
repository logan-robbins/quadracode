from __future__ import annotations

from dataclasses import replace

from quadracode_runtime.profiles import is_autonomous_mode_enabled, load_profile

from .prompts.system import SYSTEM_PROMPT
from .prompts.autonomous import AUTONOMOUS_SYSTEM_PROMPT

_PROMPT = AUTONOMOUS_SYSTEM_PROMPT if is_autonomous_mode_enabled() else SYSTEM_PROMPT
PROFILE = replace(load_profile("orchestrator"), system_prompt=_PROMPT)

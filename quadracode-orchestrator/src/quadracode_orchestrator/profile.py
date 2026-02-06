"""Runtime profile configuration for the Quadracode orchestrator.

Dynamically selects the system prompt based on whether autonomous mode is enabled,
then customizes the base orchestrator profile from quadracode_runtime. The resulting
PROFILE object is used to initialize the orchestrator's runtime environment and graph.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from quadracode_runtime.profiles import is_autonomous_mode_enabled, load_profile

from .prompts.autonomous import AUTONOMOUS_SYSTEM_PROMPT
from .prompts.system import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_autonomous = is_autonomous_mode_enabled()
_PROMPT = AUTONOMOUS_SYSTEM_PROMPT if _autonomous else SYSTEM_PROMPT

logger.info(
    "Orchestrator profile: mode=%s prompt_length=%d",
    "autonomous" if _autonomous else "standard",
    len(_PROMPT),
)

PROFILE = replace(load_profile("orchestrator"), system_prompt=_PROMPT)

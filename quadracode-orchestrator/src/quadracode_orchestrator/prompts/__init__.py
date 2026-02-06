"""Orchestrator prompt templates.

Exports:
    SYSTEM_PROMPT: Default orchestrator prompt (human-supervised mode).
    AUTONOMOUS_SYSTEM_PROMPT: Autonomous (HUMAN_OBSOLETE) mode prompt.
    HUMAN_CLONE_SYSTEM_PROMPT: Supervisor (HumanClone) skeptical reviewer persona.
    SUPERVISOR_SYSTEM_PROMPT: Preferred alias for HUMAN_CLONE_SYSTEM_PROMPT.
"""

from .autonomous import AUTONOMOUS_SYSTEM_PROMPT
from .human_clone import HUMAN_CLONE_SYSTEM_PROMPT
from .system import SYSTEM_PROMPT

# Preferred alias — new code should use SUPERVISOR_SYSTEM_PROMPT
SUPERVISOR_SYSTEM_PROMPT = HUMAN_CLONE_SYSTEM_PROMPT

__all__ = [
    "AUTONOMOUS_SYSTEM_PROMPT",
    "HUMAN_CLONE_SYSTEM_PROMPT",
    "SUPERVISOR_SYSTEM_PROMPT",
    "SYSTEM_PROMPT",
]

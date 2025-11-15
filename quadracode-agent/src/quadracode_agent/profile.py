"""
This module defines the specific runtime profile for the Quadracode agent.

It imports the base `AGENT_PROFILE` from the shared `quadracode_runtime` and 
customizes it by replacing the default system prompt with a specialized one 
defined in `.prompts.system`. This `PROFILE` object encapsulates all the 
agent-specific configurations, including its identity, tools, and core behavioral 
instructions, making it the central configuration point for the agent's runtime 
environment.
"""
from __future__ import annotations

from dataclasses import replace

from quadracode_runtime import AGENT_PROFILE

from .prompts.system import SYSTEM_PROMPT

PROFILE = replace(AGENT_PROFILE, system_prompt=SYSTEM_PROMPT)

"""
This package defines the Quadracode orchestrator, a specialized component that 
wraps the shared `quadracode-runtime` to manage and coordinate the activities of 
multiple agents.

The orchestrator is responsible for high-level task decomposition, agent 
dispatch, and the overall management of the autonomous workflow. This package 
assembles the orchestrator's specific profile and graph, configuring it with the 
necessary prompts and logic to perform its coordination role. By exposing a 
unified `PROFILE`, it provides a clear entry point for the runtime to execute 
the orchestrator's logic.
"""
from .profile import PROFILE

__all__ = ["PROFILE"]

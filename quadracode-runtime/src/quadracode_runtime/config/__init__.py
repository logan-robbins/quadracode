"""
This package defines the Pydantic-based configuration models for the various 
subsystems of the Quadracode runtime.

By centralizing these configuration schemas, this package provides a single 
source of truth for all tunable parameters of the runtime. This ensures that 
the system is configured in a consistent and validated manner, and it makes the 
runtime easier to manage and deploy. Each model in this package corresponds to a 
specific component of the runtime, such as the context engine.
"""
from .context_engine import ContextEngineConfig
from .prompt_templates import PromptTemplates

__all__ = ["ContextEngineConfig", "PromptTemplates"]

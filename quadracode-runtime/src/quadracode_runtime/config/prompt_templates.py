"""
Configurable prompt templates for all LLM interactions in the Quadracode runtime.

This module defines all prompts used throughout the context engine and related
components, making them easily configurable and maintainable. These prompts can
be overridden via configuration or UI settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(slots=True)
class PromptTemplates:
    """
    Centralized storage for all LLM prompt templates used in the runtime.
    
    Each prompt can be customized to adjust the behavior of various LLM-based
    components. Templates support variable substitution using Python string
    formatting with {variable_name} syntax.
    """
    
    # ========== Context Governor Prompts ==========
    
    governor_system_prompt: str = field(default=(
        "You are the context governor for a long-running AI agent. "
        "Your job is to keep the context window focused, concise, and free of conflicts."
    ))
    
    governor_instructions: str = field(default=(
        "Review the provided JSON summary. Produce a strict JSON object with keys "
        "'actions' and 'prompt_outline'. Each action must include 'segment_id' and 'decision' "
        "(retain, compress, summarize, isolate, externalize, discard). Optionally include "
        "'priority' or 'focus'. The prompt_outline should contain optional 'system', 'focus', "
        "and 'ordered_segments'. Do not include any additional prose."
    ))
    
    governor_input_format: str = field(default=(
        "{instructions}\n\nINPUT:\n```json\n{payload}\n```"
    ))
    
    # ========== Context Reducer Prompts ==========
    
    reducer_system_prompt: str = field(default=(
        "You condense technical context. Use structured bullet points. Keep critical details."
    ))
    
    reducer_chunk_prompt: str = field(default=(
        "Summarize the following context into concise bullet points.{focus_clause} "
        "Limit to approximately {target_tokens} tokens.\n\n```\n{chunk}\n```"
    ))
    
    reducer_focus_clause: str = field(default=(
        " Focus on {focus}."
    ))
    
    reducer_combine_prompt: str = field(default=(
        "Combine the following partial summaries into a single concise summary. "
        "Preserve key facts and actions. Use bullet points when helpful.\n\n{combined}"
    ))
    
    # ========== Conversation Management Prompts ==========

    conversation_summarization_prompt: str = field(default=(
        "Update the running conversation summary with the new messages.\n"
        "Existing Summary:\n{existing_summary}\n\n"
        "New Lines:\n{new_lines}\n\n"
        "Provide a concise, updated summary of the conversation history, merging the new information "
        "into the existing narrative. Focus on key decisions, tool outputs, and the evolving goal. "
        "Discard transient chit-chat."
    ))
    
    # ========== Context Curator Prompts (for future LLM-based curation) ==========
    
    curator_system_prompt: str = field(default=(
        "You are a context curator optimizing the working memory of an AI agent. "
        "Your decisions should maximize relevance while minimizing token usage."
    ))
    
    curator_operation_prompt: str = field(default=(
        "Evaluate the following context segment and recommend an operation "
        "(retain, compress, summarize, externalize, discard). Consider the current "
        "task focus and context pressure.\n\nSegment: {segment}\n\nFocus: {focus}\n\n"
        "Context usage: {usage_ratio}%"
    ))
    
    # ========== Context Scorer Prompts (for LLM-based scoring) ==========
    
    scorer_system_prompt: str = field(default=(
        "You evaluate context quality across multiple dimensions: relevance, coherence, "
        "completeness, freshness, diversity, and efficiency."
    ))
    
    scorer_evaluation_prompt: str = field(default=(
        "Evaluate the following context segments and provide quality scores (0-1) for each "
        "dimension. Return a JSON object with scores for: relevance, coherence, completeness, "
        "freshness, diversity, efficiency.\n\nContext:\n{context}"
    ))
    
    # ========== Progressive Loader Prompts ==========
    
    loader_system_prompt: str = field(default=(
        "You determine what additional context is needed for the current task. "
        "Recommend specific types of information to load."
    ))
    
    loader_request_prompt: str = field(default=(
        "Based on the current context and pending tasks, what additional information "
        "should be loaded? Current context types: {current_types}\n\n"
        "Pending tasks: {pending_tasks}\n\nRecommend up to {max_recommendations} "
        "context types to load, in priority order."
    ))
    
    # ========== Reflection Prompts ==========
    
    reflection_system_prompt: str = field(default=(
        "You analyze the agent's decision-making process and context management. "
        "Identify issues and recommend improvements."
    ))
    
    reflection_analysis_prompt: str = field(default=(
        "Analyze the recent decision and context state. Quality score: {quality_score}. "
        "Components: {components}. Identify any issues with: {focus_areas}. "
        "Provide specific, actionable recommendations."
    ))
    
    # ========== Compression Profiles ==========
    
    compression_profiles: Dict[str, Dict[str, any]] = field(default_factory=lambda: {
        "conservative": {
            "summary_ratio": 0.7,
            "preserve_detail": True,
            "prioritize_recent": True,
            "keep_structure": True
        },
        "balanced": {
            "summary_ratio": 0.5,
            "preserve_detail": True,
            "prioritize_recent": False,
            "keep_structure": False
        },
        "aggressive": {
            "summary_ratio": 0.3,
            "preserve_detail": False,
            "prioritize_recent": False,
            "keep_structure": False
        },
        "extreme": {
            "summary_ratio": 0.2,
            "preserve_detail": False,
            "prioritize_recent": False,
            "keep_structure": False
        }
    })
    
    # ========== Domain-Specific Templates ==========
    
    domain_templates: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "code": {
            "focus": "function signatures, logic flow, and dependencies",
            "summary_style": "preserve exact syntax and structure",
            "priority": "implementation details and error handling"
        },
        "documentation": {
            "focus": "key concepts, examples, and API references",
            "summary_style": "maintain hierarchical structure",
            "priority": "usage patterns and constraints"
        },
        "conversation": {
            "focus": "user intent, decisions made, and action items",
            "summary_style": "chronological with clear outcomes",
            "priority": "agreements and next steps"
        },
        "test_results": {
            "focus": "failures, error messages, and stack traces",
            "summary_style": "structured with pass/fail statistics",
            "priority": "failing tests and root causes"
        },
        "tool_output": {
            "focus": "results, side effects, and return values",
            "summary_style": "concise with key data preserved",
            "priority": "successful operations and errors"
        }
    })
    
    # ========== Adaptive Prompt Modifiers ==========
    
    pressure_modifiers: Dict[str, str] = field(default_factory=lambda: {
        "low": "Be thorough and preserve detail where valuable.",
        "medium": "Balance detail with conciseness.",
        "high": "Aggressively compress while keeping essential facts.",
        "critical": "Maximum compression - only the most critical information."
    })
    
    def get_prompt(self, template_name: str, **kwargs) -> str:
        """
        Get a formatted prompt template with variable substitution.
        
        Args:
            template_name: The name of the template attribute
            **kwargs: Variables to substitute in the template
            
        Returns:
            The formatted prompt string
        """
        template = getattr(self, template_name, None)
        if template is None:
            raise ValueError(f"Unknown prompt template: {template_name}")
        
        if callable(template):
            return template(**kwargs)
        
        return template.format(**kwargs)
    
    def get_compression_profile(self, profile_name: str = "balanced") -> Dict[str, any]:
        """Get compression settings for a named profile."""
        return self.compression_profiles.get(profile_name, self.compression_profiles["balanced"])
    
    def get_domain_template(self, domain: str) -> Dict[str, str]:
        """Get domain-specific prompt modifiers."""
        return self.domain_templates.get(domain, self.domain_templates.get("code", {}))
    
    def get_pressure_modifier(self, context_ratio: float) -> str:
        """Get appropriate pressure modifier based on context usage ratio."""
        if context_ratio < 0.5:
            return self.pressure_modifiers["low"]
        elif context_ratio < 0.75:
            return self.pressure_modifiers["medium"]
        elif context_ratio < 0.9:
            return self.pressure_modifiers["high"]
        else:
            return self.pressure_modifiers["critical"]
    
    def customize_for_domain(self, base_prompt: str, domain: str) -> str:
        """
        Enhance a base prompt with domain-specific guidance.
        
        Args:
            base_prompt: The base prompt to enhance
            domain: The domain type (code, documentation, etc.)
            
        Returns:
            Enhanced prompt with domain-specific guidance
        """
        domain_info = self.get_domain_template(domain)
        enhancements = []
        
        if "focus" in domain_info:
            enhancements.append(f"Focus on: {domain_info['focus']}")
        if "summary_style" in domain_info:
            enhancements.append(f"Style: {domain_info['summary_style']}")
        if "priority" in domain_info:
            enhancements.append(f"Prioritize: {domain_info['priority']}")
        
        if enhancements:
            return f"{base_prompt}\n\nDomain-specific guidance:\n" + "\n".join(f"- {e}" for e in enhancements)
        return base_prompt
    
    def to_dict(self) -> Dict[str, any]:
        """Convert all prompts to a dictionary for serialization."""
        return {
            "governor_system_prompt": self.governor_system_prompt,
            "governor_instructions": self.governor_instructions,
            "governor_input_format": self.governor_input_format,
            "reducer_system_prompt": self.reducer_system_prompt,
            "reducer_chunk_prompt": self.reducer_chunk_prompt,
            "reducer_focus_clause": self.reducer_focus_clause,
            "reducer_combine_prompt": self.reducer_combine_prompt,
            "curator_system_prompt": self.curator_system_prompt,
            "curator_operation_prompt": self.curator_operation_prompt,
            "scorer_system_prompt": self.scorer_system_prompt,
            "scorer_evaluation_prompt": self.scorer_evaluation_prompt,
            "loader_system_prompt": self.loader_system_prompt,
            "loader_request_prompt": self.loader_request_prompt,
            "reflection_system_prompt": self.reflection_system_prompt,
            "reflection_analysis_prompt": self.reflection_analysis_prompt,
            "compression_profiles": self.compression_profiles,
            "domain_templates": self.domain_templates,
            "pressure_modifiers": self.pressure_modifiers,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> "PromptTemplates":
        """Create PromptTemplates instance from dictionary."""
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})

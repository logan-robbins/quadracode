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
        "You are the Context Governor for an AI orchestration system.\n\n"
        "Your role is to manage the context window to ensure the AI maintains awareness "
        "of the full conversation while staying within token limits. You decide which "
        "context segments to retain, compress, or discard based on their relevance, "
        "recency, and importance to the current task.\n\n"
        "Key principles:\n"
        "- User identity and preferences must ALWAYS be preserved\n"
        "- Recent messages and current task context have highest priority\n"
        "- Historical context should be compressed into summaries\n"
        "- Technical details can be externalized if not immediately needed"
    ))
    
    governor_instructions: str = field(default=(
        "Analyze the context segments and create a management plan.\n\n"
        "For each segment, decide one of:\n"
        "- 'retain': Keep as-is (for critical/recent content)\n"
        "- 'compress': Reduce size while keeping key facts\n"
        "- 'summarize': Create brief summary of main points\n"
        "- 'externalize': Store externally, keep reference only\n"
        "- 'discard': Remove if no longer relevant\n\n"
        "Return a JSON object with:\n"
        "- 'actions': Array of {segment_id, decision, priority}\n"
        "- 'prompt_outline': {system, focus, ordered_segments}\n\n"
        "Prioritize preserving user context and current task information."
    ))
    
    governor_input_format: str = field(default=(
        "{instructions}\n\nCONTEXT STATE:\n```json\n{payload}\n```\n\n"
        "OUTPUT (JSON only):"
    ))
    
    # Message that gets added to the main orchestrator's prompt when context management is active
    governor_driver_message: str = field(default=(
        "📝 Context Management Active:\n"
        "- Older conversation has been compressed into a summary to fit token limits\n"
        "- The summary preserves user information, key decisions, and task context\n"
        "- Recent messages are kept in full for immediate context\n"
        "- Use both the summary and recent messages to maintain conversation continuity\n"
        "- If the user references earlier conversation, check the summary first"
    ))
    
    # ========== Context Reducer Prompts ==========
    
    reducer_system_prompt: str = field(default=(
        "You are a Context Compression Specialist for an AI system.\n\n"
        "Your role is to intelligently compress context while preserving ALL critical information:\n"
        "- User identity, names, preferences, and personal information\n"
        "- Key decisions and agreements made\n"
        "- Current task objectives and requirements\n"
        "- Important tool outputs and results\n"
        "- Error states and unresolved issues\n\n"
        "Use structured formats (bullets, sections) for clarity.\n"
        "NEVER lose user-specific information or current task context."
    ))
    
    reducer_chunk_prompt: str = field(default=(
        "Compress the following content to approximately {target_tokens} tokens.{focus_clause}\n\n"
        "MUST PRESERVE:\n"
        "- Any user names, identities, or personal information\n"
        "- Current task or question being addressed\n"
        "- Key decisions, results, or errors\n\n"
        "Content to compress:\n```\n{chunk}\n```\n\n"
        "Compressed version:"
    ))
    
    reducer_focus_clause: str = field(default=(
        "\n\nSpecial focus: {focus}"
    ))
    
    reducer_combine_prompt: str = field(default=(
        "Merge these partial summaries into a unified, coherent summary.\n\n"
        "Requirements:\n"
        "- Eliminate redundancy while keeping all unique information\n"
        "- Maintain chronological flow where relevant\n"
        "- Preserve ALL user-specific details\n"
        "- Keep critical decisions and outcomes\n\n"
        "Partial summaries to merge:\n{combined}\n\n"
        "Unified summary:"
    ))
    
    # ========== Conversation Management Prompts ==========

    conversation_summarization_prompt: str = field(default=(
        "Create an updated conversation summary that will serve as the AI's memory.\n\n"
        "CRITICAL PRESERVATION REQUIREMENTS:\n"
        "1. **User Identity**: ALWAYS preserve names, roles, and any personal information\n"
        "2. **User Preferences**: Keep all stated preferences, requirements, or constraints\n"
        "3. **Current Context**: What the user is currently trying to accomplish\n"
        "4. **Key History**: Important decisions, agreements, or results from earlier\n\n"
        "Existing Summary:\n{existing_summary}\n\n"
        "New Messages to Incorporate:\n{new_lines}\n\n"
        "Instructions:\n"
        "- Merge new information with existing summary\n"
        "- Use clear sections (e.g., 'User Information:', 'Current Task:', 'History:')\n"
        "- Be concise but NEVER lose critical user details\n"
        "- Include specific names, numbers, and decisions\n"
        "- Note any unresolved questions or pending tasks\n\n"
        "Updated Summary:"
    ))
    
    # ========== Context Curator Prompts (for future LLM-based curation) ==========
    
    curator_system_prompt: str = field(default=(
        "You are the Context Curator, responsible for optimizing the AI's working memory.\n\n"
        "Your decisions directly impact the AI's ability to:\n"
        "- Remember user information and conversation history\n"
        "- Access relevant technical context\n"
        "- Complete tasks effectively\n\n"
        "Prioritize user context and current task relevance above all else."
    ))
    
    curator_operation_prompt: str = field(default=(
        "Evaluate this context segment and recommend the best operation.\n\n"
        "Segment ID: {segment}\n"
        "Current Focus: {focus}\n"
        "Context Usage: {usage_ratio}% of available space\n\n"
        "Options:\n"
        "- 'retain': Keep unchanged (for critical/active content)\n"
        "- 'compress': Reduce size but keep main points\n"
        "- 'summarize': Create brief summary only\n"
        "- 'externalize': Move to storage, keep reference\n"
        "- 'discard': Remove entirely (only if truly irrelevant)\n\n"
        "Recommendation (with brief reason):"
    ))
    
    # ========== Context Scorer Prompts (for LLM-based scoring) ==========
    
    scorer_system_prompt: str = field(default=(
        "You are the Context Quality Scorer.\n\n"
        "Evaluate context across six dimensions:\n"
        "- **Relevance**: Does context relate to current task?\n"
        "- **Coherence**: Is context well-organized and clear?\n"
        "- **Completeness**: Do we have all needed information?\n"
        "- **Freshness**: Is context up-to-date?\n"
        "- **Diversity**: Do we have different types of useful context?\n"
        "- **Efficiency**: Are we using token space wisely?\n\n"
        "Score each from 0.0 (poor) to 1.0 (excellent)."
    ))
    
    scorer_evaluation_prompt: str = field(default=(
        "Score the quality of this context for helping the AI respond effectively.\n\n"
        "Context to evaluate:\n{context}\n\n"
        "Scoring criteria:\n"
        "- Relevance (0-1): How well does this support the current task?\n"
        "- Coherence (0-1): How well organized and clear is it?\n"
        "- Completeness (0-1): Do we have all critical information?\n"
        "- Freshness (0-1): How current is this information?\n"
        "- Diversity (0-1): Do we have good variety of context types?\n"
        "- Efficiency (0-1): How well are we using available space?\n\n"
        "Return JSON with scores:\n"
        '{"relevance": 0.X, "coherence": 0.X, "completeness": 0.X, "freshness": 0.X, "diversity": 0.X, "efficiency": 0.X}'
    ))
    
    # ========== Progressive Loader Prompts ==========
    
    loader_system_prompt: str = field(default=(
        "You are the Context Loader, responsible for identifying gaps in the AI's knowledge.\n\n"
        "Your job is to determine what additional context would help the AI:\n"
        "- Answer the user's current question\n"
        "- Complete the active task\n"
        "- Maintain conversation continuity\n\n"
        "Be strategic about what to load given limited token space."
    ))
    
    loader_request_prompt: str = field(default=(
        "Identify what additional context would be most helpful.\n\n"
        "Currently Loaded: {current_types}\n"
        "Pending Tasks: {pending_tasks}\n"
        "Available Space: Limited to {max_recommendations} new segments\n\n"
        "What specific information should we load? Priority order:\n"
        "1. Information directly needed for the current question/task\n"
        "2. Recent context that provides continuity\n"
        "3. Background knowledge if space allows\n\n"
        "Recommendations (most important first):"
    ))
    
    # ========== Reflection Prompts ==========
    
    reflection_system_prompt: str = field(default=(
        "You are the Context Quality Analyst.\n\n"
        "Your role is to ensure the AI maintains high-quality context that:\n"
        "- Preserves critical user information\n"
        "- Supports effective task completion\n"
        "- Stays within token limits\n"
        "- Remains coherent and relevant\n\n"
        "Identify problems early and recommend specific fixes."
    ))
    
    reflection_analysis_prompt: str = field(default=(
        "Analyze the current context quality and identify improvements needed.\n\n"
        "Current Metrics:\n"
        "- Overall Quality Score: {quality_score}\n"
        "- Component Scores: {components}\n"
        "- Focus Areas: {focus_areas}\n\n"
        "Evaluate:\n"
        "1. Is user context being preserved properly?\n"
        "2. Is current task context clear and complete?\n"
        "3. Are we within token budget efficiently?\n"
        "4. What critical information might be missing?\n\n"
        "Provide 3-5 specific, actionable recommendations:"
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

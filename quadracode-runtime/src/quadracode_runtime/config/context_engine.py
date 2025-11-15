"""
This module defines the configuration schema for the ContextEngine, a core 
component of the Quadracode runtime responsible for managing the contextual 
information available to the language models.

The `ContextEngineConfig` class provides a centralized, strongly-typed
dataclass for all the tunable parameters that govern the context engine's 
behavior. This includes settings for token limits, memory management, quality 
thresholds, and the various sub-frameworks that the context engine employs, such 
as ACE (Adaptive Context Engineering) and MemAct (Memory Activation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(slots=True)
class ContextEngineConfig:
    """
    Provides a structured configuration for the ContextEngine component.

    This dataclass encapsulates all the settings that control the behavior of 
    the context engine. It is designed to be instantiated with sensible 
    defaults, which can be selectively overridden by environment variables for 
    flexible deployment.

    Attributes:
        context_window_max: The maximum number of tokens allowed in the context window.
        target_context_size: The desired size of the context window to aim for.
        quality_threshold: The minimum quality score for a context segment to be included.
        ... and many others, see the class definition for a full list.
    """

    # Token limits
    context_window_max: int = 128_000
    target_context_size: int = 10_000

    # Quality thresholds
    quality_threshold: float = 0.7
    min_segment_priority: int = 3

    # Memory settings
    external_memory_path: str = "/shared/context_memory"
    max_checkpoints: int = 100
    checkpoint_frequency: int = 10

    # Compression settings
    compression_ratio_target: float = 0.3

    # Progressive loading
    progressive_batch_size: int = 5_000
    prefetch_depth: int = 2

    # ACE Framework
    evolution_frequency: int = 5
    reflection_depth: int = 3
    curation_rules_max: int = 20

    # MemAct Framework
    operation_learning_rate: float = 0.1
    operation_exploration_rate: float = 0.2

    # Metrics & observability
    metrics_enabled: bool = True
    metrics_stream_key: str = "qc:context:metrics"
    metrics_redis_url: str = "redis://redis:6379/0"
    metrics_emit_mode: str = "stream"  # stream | log
    autonomous_metrics_stream_key: str = "qc:autonomous:events"

    # Project awareness
    project_root: str = field(default_factory=lambda: str(Path.cwd()))
    documentation_paths: List[str] = field(
        default_factory=lambda: [
            "README.md",
            "private/HUMAN_OBSOLETE.md",
            "private/CONTEXT_ENG.md",
        ]
    )
    code_search_max_results: int = 3
    code_search_extensions: List[str] = field(
        default_factory=lambda: [".py", ".md", ".json", ".yaml", ".yml"]
    )
    code_search_exclude_dirs: List[str] = field(
        default_factory=lambda: [".git", ".venv", "node_modules", "build", "dist", "__pycache__"]
    )
    prefetch_queue_limit: int = 20
    max_tool_payload_chars: int = 4_096

    # Reducer / summarization
    reducer_model: Optional[str] = "anthropic:claude-3-haiku-20240307"
    reducer_chunk_tokens: int = 600
    reducer_target_tokens: int = 200

    # Governor / planning
    governor_model: Optional[str] = "heuristic"
    governor_max_segments: int = 12

    # Skills / progressive disclosure
    skills_paths: List[str] = field(
        default_factory=lambda: [
            "private/skills",
        ]
    )

    # Externalization persistence
    externalize_write_enabled: bool = False

    # Scoring weights (sum to 1.0)
    scoring_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "relevance": 0.25,
            "coherence": 0.20,
            "completeness": 0.15,
            "freshness": 0.15,
            "diversity": 0.15,
            "efficiency": 0.10,
        }
    )

    # Context type priorities
    context_priorities: Dict[str, int] = field(
        default_factory=lambda: {
            "system_prompt": 10,
            "current_goal": 9,
            "error_context": 8,
            "recent_decisions": 7,
            "tool_outputs": 6,
            "conversation_history": 5,
            "background_knowledge": 4,
            "archived_memory": 3,
        }
    )

    @classmethod
    def from_environment(cls) -> "ContextEngineConfig":
        """
        Creates a `ContextEngineConfig` instance, with values overridden by 
        environment variables where available.

        This factory method provides a convenient way to configure the context 
        engine in different environments without modifying the code. It checks for a 
        predefined set of environment variables and applies their values to the 
        configuration object, with robust parsing for different data types.

        Returns:
            A `ContextEngineConfig` instance with environment-specific overrides.
        """

        def _int(name: str, default: int) -> int:
            """Safely parses an integer from an environment variable."""
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            """Safely parses a float from an environment variable."""
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        def _bool(name: str, default: bool) -> bool:
            """Safely parses a boolean from an environment variable."""
            raw = os.environ.get(name)
            if raw is None:
                return default
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}

        base = cls()

        # Numeric overrides
        base.context_window_max = _int("QUADRACODE_CONTEXT_WINDOW_MAX", base.context_window_max)
        base.target_context_size = _int("QUADRACODE_TARGET_CONTEXT_SIZE", base.target_context_size)
        base.max_tool_payload_chars = _int("QUADRACODE_MAX_TOOL_PAYLOAD_CHARS", base.max_tool_payload_chars)
        base.reducer_chunk_tokens = _int("QUADRACODE_REDUCER_CHUNK_TOKENS", base.reducer_chunk_tokens)
        base.reducer_target_tokens = _int("QUADRACODE_REDUCER_TARGET_TOKENS", base.reducer_target_tokens)
        base.governor_max_segments = _int("QUADRACODE_GOVERNOR_MAX_SEGMENTS", base.governor_max_segments)
        base.quality_threshold = _float("QUADRACODE_QUALITY_THRESHOLD", base.quality_threshold)

        # String overrides
        base.reducer_model = os.environ.get("QUADRACODE_REDUCER_MODEL", base.reducer_model)
        base.governor_model = os.environ.get("QUADRACODE_GOVERNOR_MODEL", base.governor_model)
        base.metrics_emit_mode = os.environ.get("QUADRACODE_METRICS_EMIT_MODE", base.metrics_emit_mode)
        base.metrics_redis_url = os.environ.get("QUADRACODE_METRICS_REDIS_URL", base.metrics_redis_url)
        base.metrics_stream_key = os.environ.get("QUADRACODE_METRICS_STREAM_KEY", base.metrics_stream_key)
        base.autonomous_metrics_stream_key = os.environ.get(
            "QUADRACODE_AUTONOMOUS_STREAM_KEY",
            base.autonomous_metrics_stream_key,
        )

        # Booleans
        base.metrics_enabled = _bool("QUADRACODE_METRICS_ENABLED", base.metrics_enabled)
        base.externalize_write_enabled = _bool(
            "QUADRACODE_EXTERNALIZE_WRITE_ENABLED", base.externalize_write_enabled
        )

        return base

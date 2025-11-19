"""
Prompt template manager for loading, saving, and managing prompt configurations.

This module provides utilities for managing prompt templates across the runtime,
including loading from files, environment variables, Redis, and runtime updates.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
import logging
import threading
import time

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from .prompt_templates import PromptTemplates

LOGGER = logging.getLogger(__name__)

# Redis configuration keys (matching UI)
CONFIG_HASH_KEY = "qc:config:prompt_templates"
CONFIG_UPDATE_STREAM = "qc:config:updates"
CONFIG_VERSION_KEY = "qc:config:version"


class PromptManager:
    """
    Manages prompt template configurations with support for multiple sources.
    
    This class provides a centralized way to manage prompt templates, supporting:
    - Loading from Redis (if available)
    - Loading from configuration files (JSON/YAML)
    - Environment variable overrides
    - Runtime updates via API
    - Auto-reload on configuration changes
    - Persistence of changes
    """
    
    def __init__(self, config_path: Optional[Path] = None, enable_auto_reload: bool = True):
        """
        Initialize the PromptManager.
        
        Args:
            config_path: Optional path to a configuration file
            enable_auto_reload: Whether to enable auto-reload from Redis
        """
        self.config_path = config_path or self._get_default_config_path()
        self.templates = PromptTemplates()
        self._redis_client = None
        self._reload_thread = None
        self._stop_reload = threading.Event()
        self._last_version = 0
        
        # Initialize Redis connection if available
        if REDIS_AVAILABLE:
            self._init_redis()
        
        # Load configuration
        self._load_configuration()
        
        # Start auto-reload thread if enabled
        if enable_auto_reload and self._redis_client:
            self._start_auto_reload()
    
    def _get_default_config_path(self) -> Path:
        """Get the default configuration file path."""
        # Check environment variable first
        env_path = os.environ.get("QUADRACODE_PROMPT_CONFIG")
        if env_path:
            return Path(env_path)
        
        # Check standard locations
        possible_paths = [
            Path.home() / ".quadracode" / "prompt_templates.json",
            Path("/etc/quadracode/prompt_templates.json"),
            Path("./prompt_templates.json"),
        ]
        
        for path in possible_paths:
            if path.exists():
                return path
        
        # Default to user config directory (lazy creation to avoid blocking event loop)
        return Path.home() / ".quadracode" / "prompt_templates.json"
    
    def _init_redis(self) -> None:
        """Initialize Redis connection if available."""
        try:
            redis_host = os.environ.get("REDIS_HOST", "redis")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))
            
            self._redis_client = redis.Redis(
                host=redis_host, 
                port=redis_port, 
                decode_responses=True,
                socket_connect_timeout=2
            )
            
            # Test connection
            self._redis_client.ping()
            LOGGER.info(f"Connected to Redis at {redis_host}:{redis_port}")
            
        except Exception as e:
            LOGGER.warning(f"Redis not available: {e}. Using file-based configuration only.")
            self._redis_client = None
    
    def _load_configuration(self) -> None:
        """Load configuration from Redis, file, and environment variables."""
        loaded = False
        
        # Try loading from Redis first
        if self._redis_client:
            try:
                config_json = self._redis_client.hget(CONFIG_HASH_KEY, "current")
                if config_json:
                    data = json.loads(config_json)
                    # Remove metadata before loading
                    clean_data = {k: v for k, v in data.items() if not k.startswith("_")}
                    self.load_from_dict(clean_data)
                    self._last_version = self._get_redis_version()
                    LOGGER.info(f"Loaded prompt templates from Redis (version {self._last_version})")
                    loaded = True
            except Exception as e:
                LOGGER.debug(f"Could not load from Redis: {e}")
        
        # Fall back to file if not loaded from Redis
        if not loaded:
            # Try shared config path
            shared_path = Path("/shared/config/prompt_templates.json")
            if shared_path.exists():
                try:
                    self.load_from_file(shared_path)
                    LOGGER.info(f"Loaded prompt templates from shared path: {shared_path}")
                    loaded = True
                except Exception as e:
                    LOGGER.warning(f"Failed to load from shared path: {e}")
            
            # Try default config path
            if not loaded and self.config_path.exists():
                try:
                    self.load_from_file(self.config_path)
                    LOGGER.info(f"Loaded prompt templates from {self.config_path}")
                except Exception as e:
                    LOGGER.warning(f"Failed to load prompt templates from {self.config_path}: {e}")
        
        # Always apply environment variable overrides last
        self._apply_env_overrides()
    
    def _apply_env_overrides(self) -> None:
        """Apply environment variable overrides to prompt templates."""
        # Map of environment variables to template attributes
        env_mappings = {
            "QUADRACODE_GOVERNOR_SYSTEM": "governor_system_prompt",
            "QUADRACODE_GOVERNOR_INSTRUCTIONS": "governor_instructions",
            "QUADRACODE_REDUCER_SYSTEM": "reducer_system_prompt",
            "QUADRACODE_REDUCER_CHUNK": "reducer_chunk_prompt",
            "QUADRACODE_REDUCER_COMBINE": "reducer_combine_prompt",
            "QUADRACODE_COMPRESSION_PROFILE": "compression_profile",
        }
        
        for env_var, attr_name in env_mappings.items():
            value = os.environ.get(env_var)
            if value:
                try:
                    setattr(self.templates, attr_name, value)
                    LOGGER.debug(f"Applied {env_var} override to {attr_name}")
                except AttributeError:
                    LOGGER.warning(f"Invalid attribute {attr_name} for {env_var}")
    
    def load_from_file(self, file_path: Path) -> None:
        """
        Load prompt templates from a configuration file.
        
        Args:
            file_path: Path to the configuration file (JSON or YAML)
        """
        with open(file_path, 'r') as f:
            if file_path.suffix.lower() in ['.yaml', '.yml']:
                data = yaml.safe_load(f)
            else:
                data = json.load(f)
        
        self.load_from_dict(data)
    
    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """
        Load prompt templates from a dictionary.
        
        Args:
            data: Dictionary containing prompt template configurations
        """
        # Create new PromptTemplates instance from data
        self.templates = PromptTemplates.from_dict(data)
    
    def save_to_file(self, file_path: Optional[Path] = None) -> None:
        """
        Save current prompt templates to a configuration file.
        
        Args:
            file_path: Path to save the configuration (uses default if None)
        """
        save_path = file_path or self.config_path
        save_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = self.templates.to_dict()
        
        with open(save_path, 'w') as f:
            if save_path.suffix.lower() in ['.yaml', '.yml']:
                yaml.dump(data, f, default_flow_style=False, indent=2)
            else:
                json.dump(data, f, indent=2)
        
        LOGGER.info(f"Saved prompt templates to {save_path}")
    
    def update_template(self, template_name: str, value: str) -> None:
        """
        Update a specific prompt template.
        
        Args:
            template_name: Name of the template attribute to update
            value: New value for the template
        """
        if not hasattr(self.templates, template_name):
            raise ValueError(f"Unknown template: {template_name}")
        
        setattr(self.templates, template_name, value)
        LOGGER.debug(f"Updated template {template_name}")
    
    def update_compression_profile(self, profile_name: str, settings: Dict[str, Any]) -> None:
        """
        Update or add a compression profile.
        
        Args:
            profile_name: Name of the compression profile
            settings: Profile settings dictionary
        """
        self.templates.compression_profiles[profile_name] = settings
        LOGGER.debug(f"Updated compression profile {profile_name}")
    
    def update_domain_template(self, domain: str, template: Dict[str, str]) -> None:
        """
        Update or add a domain-specific template.
        
        Args:
            domain: Domain name (e.g., 'code', 'documentation')
            template: Domain template settings
        """
        self.templates.domain_templates[domain] = template
        LOGGER.debug(f"Updated domain template {domain}")
    
    def get_templates(self) -> PromptTemplates:
        """
        Get the current PromptTemplates instance.
        
        Returns:
            The current PromptTemplates configuration
        """
        return self.templates
    
    def export_to_dict(self) -> Dict[str, Any]:
        """
        Export current configuration as a dictionary.
        
        Returns:
            Dictionary representation of current prompt templates
        """
        return self.templates.to_dict()
    
    def validate_templates(self) -> bool:
        """
        Validate that all required templates are present and valid.
        
        Returns:
            True if all templates are valid, False otherwise
        """
        required_attrs = [
            'governor_system_prompt',
            'governor_instructions',
            'reducer_system_prompt',
            'reducer_chunk_prompt',
            'reducer_combine_prompt',
        ]
        
        for attr in required_attrs:
            if not hasattr(self.templates, attr):
                LOGGER.error(f"Missing required template: {attr}")
                return False
            
            value = getattr(self.templates, attr)
            if not value or not isinstance(value, str):
                LOGGER.error(f"Invalid template value for {attr}")
                return False
        
        return True
    
    def reset_to_defaults(self) -> None:
        """Reset all templates to their default values."""
        self.templates = PromptTemplates()
        LOGGER.info("Reset prompt templates to defaults")
    
    def get_effective_prompt(
        self,
        template_name: str,
        context_ratio: Optional[float] = None,
        domain: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Get an effective prompt with all enhancements applied.
        
        Args:
            template_name: Name of the base template
            context_ratio: Optional context usage ratio for pressure modifiers
            domain: Optional domain for domain-specific enhancements
            **kwargs: Variables to substitute in the template
        
        Returns:
            The fully formatted and enhanced prompt
        """
        # Get base prompt
        prompt = self.templates.get_prompt(template_name, **kwargs)
        
        # Apply domain enhancements if specified
        if domain:
            prompt = self.templates.customize_for_domain(prompt, domain)
        
        # Apply pressure modifier if context ratio provided
        if context_ratio is not None:
            modifier = self.templates.get_pressure_modifier(context_ratio)
            prompt = f"{prompt}\n\nContext pressure guidance: {modifier}"
        
        return prompt
    
    def _get_redis_version(self) -> int:
        """Get current version from Redis."""
        if not self._redis_client:
            return 0
        try:
            version = self._redis_client.get(CONFIG_VERSION_KEY)
            return int(version) if version else 0
        except Exception:
            return 0
    
    def _start_auto_reload(self) -> None:
        """Start the auto-reload thread to watch for configuration changes."""
        def reload_worker():
            """Worker thread that watches for configuration updates."""
            LOGGER.info("Started prompt configuration auto-reload thread")
            
            # Subscribe to reload channel
            pubsub = self._redis_client.pubsub()
            pubsub.subscribe("qc:config:reload")
            
            while not self._stop_reload.is_set():
                try:
                    # Check for pub/sub messages (immediate notifications)
                    message = pubsub.get_message(timeout=1.0)
                    if message and message['type'] == 'message':
                        LOGGER.info("Received configuration reload signal")
                        self._reload_from_redis()
                        continue
                    
                    # Also periodically check version in case we missed a pub/sub
                    current_version = self._get_redis_version()
                    if current_version > self._last_version:
                        LOGGER.info(f"Configuration version changed: {self._last_version} -> {current_version}")
                        self._reload_from_redis()
                        
                except Exception as e:
                    LOGGER.error(f"Error in auto-reload thread: {e}")
                    time.sleep(5)  # Back off on error
            
            pubsub.close()
            LOGGER.info("Stopped prompt configuration auto-reload thread")
        
        self._reload_thread = threading.Thread(target=reload_worker, daemon=True)
        self._reload_thread.start()
    
    def _reload_from_redis(self) -> None:
        """Reload configuration from Redis."""
        try:
            config_json = self._redis_client.hget(CONFIG_HASH_KEY, "current")
            if config_json:
                data = json.loads(config_json)
                clean_data = {k: v for k, v in data.items() if not k.startswith("_")}
                self.load_from_dict(clean_data)
                self._last_version = self._get_redis_version()
                
                # Re-apply environment overrides
                self._apply_env_overrides()
                
                LOGGER.info(f"Reloaded prompt templates from Redis (version {self._last_version})")
        except Exception as e:
            LOGGER.error(f"Failed to reload from Redis: {e}")
    
    def stop(self) -> None:
        """Stop the auto-reload thread."""
        if self._reload_thread:
            self._stop_reload.set()
            self._reload_thread.join(timeout=5)
            LOGGER.info("Stopped prompt manager")
    
    def __del__(self):
        """Cleanup on deletion."""
        self.stop()


# Global instance for easy access
_global_manager: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """
    Get the global PromptManager instance.
    
    Returns:
        The global PromptManager instance
    """
    global _global_manager
    if _global_manager is None:
        _global_manager = PromptManager()
    return _global_manager


def reload_prompts() -> None:
    """Reload prompts from configuration."""
    global _global_manager
    if _global_manager:
        _global_manager.stop()
    _global_manager = PromptManager()
    LOGGER.info("Reloaded prompt templates")

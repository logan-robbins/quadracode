"""
Configuration synchronization utilities for prompt templates.

This module provides functions to sync prompt configurations between the UI
and the runtime context engine via Redis and persistent storage.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
import redis
from datetime import datetime, timezone

from quadracode_ui.config import REDIS_HOST, REDIS_PORT

LOGGER = logging.getLogger(__name__)

# Redis keys for configuration
CONFIG_HASH_KEY = "qc:config:prompt_templates"
CONFIG_UPDATE_STREAM = "qc:config:updates"
CONFIG_VERSION_KEY = "qc:config:version"

# Shared configuration file path
SHARED_CONFIG_PATH = Path("/shared/config/prompt_templates.json")


class ConfigSync:
    """Manages synchronization of prompt configurations between UI and runtime."""
    
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        """
        Initialize the ConfigSync manager.
        
        Args:
            redis_client: Optional Redis client instance
        """
        self.redis_client = redis_client or redis.Redis(
            host=REDIS_HOST, 
            port=REDIS_PORT, 
            decode_responses=True
        )
    
    def save_prompts(self, prompt_templates: Dict[str, Any]) -> bool:
        """
        Save prompt templates to both Redis and persistent storage.
        
        This method:
        1. Saves to Redis for immediate availability
        2. Publishes an update event
        3. Saves to shared file system for persistence
        
        Args:
            prompt_templates: Dictionary of prompt template configurations
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Add metadata
            config_with_metadata = {
                **prompt_templates,
                "_metadata": {
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "version": self._increment_version(),
                    "source": "ui"
                }
            }
            
            # 1. Save to Redis hash
            config_json = json.dumps(config_with_metadata)
            self.redis_client.hset(CONFIG_HASH_KEY, "current", config_json)
            
            # 2. Publish update event to stream
            update_event = {
                "type": "prompt_template_update",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": config_with_metadata["_metadata"]["version"],
                "source": "ui"
            }
            self.redis_client.xadd(CONFIG_UPDATE_STREAM, update_event, maxlen=100)
            
            # 3. Save to shared filesystem
            self._save_to_file(config_with_metadata)
            
            # 4. Also save backup to user's home directory
            self._save_backup(config_with_metadata)
            
            LOGGER.info(f"Saved prompt templates (version {config_with_metadata['_metadata']['version']})")
            return True
            
        except Exception as e:
            LOGGER.error(f"Failed to save prompt templates: {e}")
            return False
    
    def load_prompts(self) -> Dict[str, Any]:
        """
        Load prompt templates from Redis or file system.
        
        Tries in order:
        1. Redis (most recent)
        2. Shared file system
        3. Default templates
        
        Returns:
            Dictionary of prompt template configurations
        """
        try:
            # Try Redis first
            config_json = self.redis_client.hget(CONFIG_HASH_KEY, "current")
            if config_json:
                config = json.loads(config_json)
                LOGGER.info(f"Loaded prompts from Redis (version {config.get('_metadata', {}).get('version', 'unknown')})")
                return self._strip_metadata(config)
            
            # Try shared file
            if SHARED_CONFIG_PATH.exists():
                with open(SHARED_CONFIG_PATH, 'r') as f:
                    config = json.load(f)
                LOGGER.info("Loaded prompts from shared file")
                return self._strip_metadata(config)
            
            # Return defaults
            LOGGER.info("Using default prompt templates")
            return self._get_defaults()
            
        except Exception as e:
            LOGGER.error(f"Failed to load prompt templates: {e}")
            return self._get_defaults()
    
    def watch_for_updates(self, callback=None) -> Optional[Dict[str, Any]]:
        """
        Check for configuration updates from other sources.
        
        Args:
            callback: Optional callback function to call with updates
            
        Returns:
            Updated configuration if available, None otherwise
        """
        try:
            # Read from update stream
            messages = self.redis_client.xread(
                {CONFIG_UPDATE_STREAM: "$"},
                block=100  # 100ms timeout
            )
            
            if messages:
                # Get the latest update
                for stream_name, stream_messages in messages:
                    for message_id, data in stream_messages:
                        if data.get("type") == "prompt_template_update":
                            # Load the updated config
                            updated_config = self.load_prompts()
                            
                            if callback:
                                callback(updated_config)
                            
                            return updated_config
            
            return None
            
        except Exception as e:
            LOGGER.debug(f"Error checking for updates: {e}")
            return None
    
    def get_current_version(self) -> int:
        """
        Get the current configuration version number.
        
        Returns:
            Version number, or 0 if not set
        """
        try:
            version = self.redis_client.get(CONFIG_VERSION_KEY)
            return int(version) if version else 0
        except (redis.RedisError, ValueError):
            return 0
    
    def _increment_version(self) -> int:
        """Increment and return the configuration version."""
        try:
            return self.redis_client.incr(CONFIG_VERSION_KEY)
        except redis.RedisError:
            return 1
    
    def _save_to_file(self, config: Dict[str, Any]) -> None:
        """Save configuration to shared file system."""
        try:
            # Create directory if it doesn't exist
            SHARED_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            
            # Write atomically by using a temp file
            temp_path = SHARED_CONFIG_PATH.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            # Atomic rename
            temp_path.replace(SHARED_CONFIG_PATH)
            
            LOGGER.info(f"Saved prompts to {SHARED_CONFIG_PATH}")
            
        except Exception as e:
            LOGGER.warning(f"Failed to save to shared file: {e}")
    
    def _save_backup(self, config: Dict[str, Any]) -> None:
        """Save backup configuration to user's home directory."""
        try:
            backup_path = Path.home() / ".quadracode" / "prompt_templates_backup.json"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(backup_path, 'w') as f:
                json.dump(config, f, indent=2)
                
        except Exception as e:
            LOGGER.debug(f"Failed to save backup: {e}")
    
    def _strip_metadata(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Remove internal metadata from configuration."""
        return {k: v for k, v in config.items() if not k.startswith("_")}
    
    def _get_defaults(self) -> Dict[str, Any]:
        """Get default prompt template configuration."""
        return {
            "governor": {
                "system_prompt": "You are the context governor for a long-running AI agent. Your job is to keep the context window focused, concise, and free of conflicts.",
                "instructions": "Review the provided JSON summary. Produce a strict JSON object with keys 'actions' and 'prompt_outline'."
            },
            "reducer": {
                "system_prompt": "You condense technical context. Use structured bullet points. Keep critical details.",
                "chunk_prompt": "Summarize the following context into concise bullet points. Limit to approximately {target_tokens} tokens.",
                "combine_prompt": "Combine the following partial summaries into a single concise summary."
            },
            "compression_profiles": {
                "conservative": {"summary_ratio": 0.7, "preserve_detail": True},
                "balanced": {"summary_ratio": 0.5, "preserve_detail": True},
                "aggressive": {"summary_ratio": 0.3, "preserve_detail": False},
                "extreme": {"summary_ratio": 0.2, "preserve_detail": False}
            }
        }
    
    def publish_reload_signal(self) -> bool:
        """
        Publish a signal to notify runtime components to reload configuration.
        
        Returns:
            True if signal was published successfully
        """
        try:
            reload_event = {
                "type": "reload_prompts",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": self.get_current_version()
            }
            self.redis_client.xadd(CONFIG_UPDATE_STREAM, reload_event, maxlen=100)
            
            # Also publish to pub/sub for immediate notification
            self.redis_client.publish("qc:config:reload", json.dumps(reload_event))
            
            return True
            
        except Exception as e:
            LOGGER.error(f"Failed to publish reload signal: {e}")
            return False


def get_config_sync(redis_client: Optional[redis.Redis] = None) -> ConfigSync:
    """
    Get a ConfigSync instance.
    
    Args:
        redis_client: Optional Redis client
        
    Returns:
        ConfigSync instance
    """
    return ConfigSync(redis_client)

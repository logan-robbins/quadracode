"""
Test prompt configuration persistence between UI and runtime.

This test verifies that prompt changes made in the UI are correctly
persisted and loaded by the runtime context engine.
"""

import json
import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Mock Redis if not available
try:
    import redis
except ImportError:
    redis = None


@pytest.fixture
def mock_redis():
    """Create a mock Redis client for testing."""
    client = MagicMock()
    storage = {}
    
    def hset(key, field, value):
        if key not in storage:
            storage[key] = {}
        storage[key][field] = value
        return 1
    
    def hget(key, field):
        return storage.get(key, {}).get(field)
    
    def incr(key):
        storage[key] = storage.get(key, 0) + 1
        return storage[key]
    
    def get(key):
        return storage.get(key)
    
    client.hset = hset
    client.hget = hget
    client.incr = incr
    client.get = get
    client.xadd = MagicMock(return_value="1-0")
    client.publish = MagicMock(return_value=1)
    client.ping = MagicMock(return_value=True)
    
    return client


def test_config_sync_save(mock_redis):
    """Test that ConfigSync correctly saves prompts to multiple locations."""
    from quadracode_ui.utils.config_sync import ConfigSync
    
    with patch('quadracode_ui.utils.config_sync.Path') as mock_path:
        # Mock the shared config path
        mock_path.return_value.parent.mkdir = MagicMock()
        
        sync = ConfigSync(redis_client=mock_redis)
        
        test_prompts = {
            "governor": {
                "system_prompt": "Test governor prompt",
                "instructions": "Test instructions"
            },
            "compression_profiles": {
                "test": {"summary_ratio": 0.5}
            }
        }
        
        # Save prompts
        result = sync.save_prompts(test_prompts)
        assert result is True
        
        # Verify Redis was updated
        assert mock_redis.hset.called
        saved_json = mock_redis.hget("qc:config:prompt_templates", "current")
        assert saved_json is not None
        
        saved_data = json.loads(saved_json)
        assert saved_data["governor"]["system_prompt"] == "Test governor prompt"
        assert "_metadata" in saved_data
        assert saved_data["_metadata"]["source"] == "ui"
        
        # Verify event was published
        assert mock_redis.xadd.called
        assert mock_redis.publish.called


def test_config_sync_load(mock_redis):
    """Test that ConfigSync correctly loads prompts from Redis."""
    from quadracode_ui.utils.config_sync import ConfigSync
    
    # Pre-populate Redis
    test_data = {
        "governor": {"system_prompt": "Loaded prompt"},
        "_metadata": {"version": 5}
    }
    mock_redis.hset("qc:config:prompt_templates", "current", json.dumps(test_data))
    
    sync = ConfigSync(redis_client=mock_redis)
    loaded = sync.load_prompts()
    
    assert loaded["governor"]["system_prompt"] == "Loaded prompt"
    assert "_metadata" not in loaded  # Metadata should be stripped


def test_prompt_manager_redis_load():
    """Test that PromptManager loads from Redis when available."""
    from quadracode_runtime.config.prompt_manager import PromptManager
    
    with patch('quadracode_runtime.config.prompt_manager.redis') as mock_redis_module:
        mock_client = mock_redis()
        
        # Pre-populate with test data
        test_data = {
            "governor_system_prompt": "Redis loaded prompt",
            "compression_profiles": {
                "test": {"summary_ratio": 0.3}
            }
        }
        mock_client.hset("qc:config:prompt_templates", "current", json.dumps(test_data))
        
        mock_redis_module.Redis.return_value = mock_client
        
        # Create manager with auto-reload disabled for testing
        manager = PromptManager(enable_auto_reload=False)
        
        # Check that templates were loaded
        templates = manager.get_templates()
        assert templates.governor_system_prompt == "Redis loaded prompt"


def test_prompt_manager_file_fallback():
    """Test that PromptManager falls back to file when Redis unavailable."""
    from quadracode_runtime.config.prompt_manager import PromptManager
    
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test_prompts.json"
        
        # Create test config file
        test_data = {
            "governor_system_prompt": "File loaded prompt",
            "reducer_system_prompt": "Test reducer"
        }
        config_path.write_text(json.dumps(test_data))
        
        # Create manager without Redis
        with patch('quadracode_runtime.config.prompt_manager.REDIS_AVAILABLE', False):
            manager = PromptManager(config_path=config_path, enable_auto_reload=False)
            
            templates = manager.get_templates()
            assert templates.governor_system_prompt == "File loaded prompt"
            assert templates.reducer_system_prompt == "Test reducer"


def test_prompt_manager_env_override():
    """Test that environment variables override loaded configuration."""
    from quadracode_runtime.config.prompt_manager import PromptManager
    
    with patch.dict('os.environ', {
        'QUADRACODE_GOVERNOR_SYSTEM': 'Env override prompt',
        'QUADRACODE_REDUCER_SYSTEM': 'Env reducer'
    }):
        with patch('quadracode_runtime.config.prompt_manager.REDIS_AVAILABLE', False):
            manager = PromptManager(enable_auto_reload=False)
            
            templates = manager.get_templates()
            assert templates.governor_system_prompt == "Env override prompt"
            assert templates.reducer_system_prompt == "Env reducer"


def test_context_engine_config_lazy_load():
    """Test that ContextEngineConfig lazily loads prompts from PromptManager."""
    from quadracode_runtime.config.context_engine import ContextEngineConfig
    from quadracode_runtime.config.prompt_manager import PromptManager
    
    with patch('quadracode_runtime.config.prompt_manager.REDIS_AVAILABLE', False):
        # Create config
        config = ContextEngineConfig()
        
        # Access prompt_templates property
        templates = config.prompt_templates
        assert templates is not None
        assert hasattr(templates, 'governor_system_prompt')
        
        # Verify it's cached
        templates2 = config.prompt_templates
        assert templates is templates2  # Same object


def test_end_to_end_persistence():
    """Test the complete flow from UI save to runtime reload."""
    if redis is None:
        pytest.skip("Redis not available")
    
    from quadracode_ui.utils.config_sync import ConfigSync
    from quadracode_runtime.config.prompt_manager import PromptManager
    
    # Use in-memory mock Redis
    mock_client = mock_redis()
    
    # UI side: Save configuration
    ui_sync = ConfigSync(redis_client=mock_client)
    test_prompts = {
        "governor": {
            "system_prompt": "E2E test prompt",
            "instructions": "E2E instructions"
        }
    }
    
    assert ui_sync.save_prompts(test_prompts)
    
    # Runtime side: Load configuration
    with patch('quadracode_runtime.config.prompt_manager.redis.Redis', return_value=mock_client):
        manager = PromptManager(enable_auto_reload=False)
        templates = manager.get_templates()
        
        # Verify the prompt made it through
        assert templates.governor_system_prompt == "E2E test prompt"
        assert templates.governor_instructions == "E2E instructions"


def test_version_tracking(mock_redis):
    """Test that version numbers increment correctly."""
    from quadracode_ui.utils.config_sync import ConfigSync
    
    sync = ConfigSync(redis_client=mock_redis)
    
    # Initial version
    version1 = sync.get_current_version()
    
    # Save should increment version
    sync.save_prompts({"test": "data1"})
    version2 = sync.get_current_version()
    assert version2 == version1 + 1
    
    # Another save should increment again
    sync.save_prompts({"test": "data2"})
    version3 = sync.get_current_version()
    assert version3 == version2 + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

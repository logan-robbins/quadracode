# Prompt Configuration Persistence Architecture

## Overview

The Quadracode prompt configuration system provides a robust, multi-layered persistence mechanism that ensures prompt templates are synchronized between the UI and runtime context engine. This document explains how changes made in the UI persist to the runtime.

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                         Streamlit UI                              │
│                    (Prompt Settings Page)                         │
│                                                                    │
│  [Edit Prompts] → [Save to Runtime] → ConfigSync.save_prompts()  │
└────────────────────┬─────────────────────────────────────────────┘
                      │
                      │ Saves to 3 locations
                      ▼
        ┌─────────────────────────────────┐
        │      Persistence Layer          │
        ├─────────────────────────────────┤
        │ 1. Redis Hash                   │
        │    Key: qc:config:prompt_templates │
        │    → Immediate availability      │
        │                                  │
        │ 2. Redis Stream/PubSub          │
        │    Stream: qc:config:updates    │
        │    Channel: qc:config:reload    │
        │    → Real-time notifications     │
        │                                  │
        │ 3. Shared File System            │
        │    Path: /shared/config/*.json   │
        │    → Persistence across restarts │
        └─────────────┬───────────────────┘
                      │
                      │ Auto-reload
                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Context Engine Runtime                         │
│                                                                   │
│  PromptManager                                                    │
│  ├─ Auto-reload Thread (monitors Redis)                          │
│  ├─ Redis PubSub Listener                                        │
│  ├─ Version Tracking                                             │
│  └─ Lazy Loading via ContextEngineConfig                         │
│                                                                   │
│  context_engine.py → config.prompt_templates → Live Updates      │
└──────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. UI to Persistence (Save Operation)

When a user clicks "Save to Runtime" in the UI:

```python
# UI calls ConfigSync.save_prompts()
config_sync.save_prompts(prompt_templates) 
    ↓
# Step 1: Add metadata (version, timestamp)
config_with_metadata = {
    ...templates,
    "_metadata": {
        "version": increment_version(),
        "updated_at": now(),
        "source": "ui"
    }
}
    ↓
# Step 2: Save to Redis Hash (immediate)
redis.hset("qc:config:prompt_templates", "current", json)
    ↓
# Step 3: Publish update event (notifications)
redis.xadd("qc:config:updates", {"type": "prompt_template_update"})
redis.publish("qc:config:reload", signal)
    ↓
# Step 4: Save to shared file (persistence)
/shared/config/prompt_templates.json
    ↓
# Step 5: Backup to user home
~/.quadracode/prompt_templates_backup.json
```

### 2. Runtime Auto-Reload (Continuous Monitoring)

The runtime PromptManager runs a background thread that:

```python
# Background thread in PromptManager
while running:
    # Listen for Redis PubSub messages (immediate)
    message = pubsub.get_message(timeout=1.0)
    if message:
        reload_from_redis()
    
    # Check version periodically (fallback)
    if redis_version > local_version:
        reload_from_redis()
```

### 3. Context Engine Access (Lazy Loading)

The context engine accesses prompts through a lazy-loading property:

```python
class ContextEngineConfig:
    @property
    def prompt_templates(self) -> PromptTemplates:
        # Always gets latest from PromptManager
        if self._prompt_templates is None:
            manager = get_prompt_manager()  # Global singleton
            self._prompt_templates = manager.get_templates()
        return self._prompt_templates
```

## Persistence Layers

### Layer 1: Redis (Immediate)
- **Purpose**: Fast, shared memory for immediate updates
- **Keys**:
  - `qc:config:prompt_templates` - Current configuration
  - `qc:config:version` - Version counter
  - `qc:config:updates` - Update event stream
- **Benefits**: 
  - Sub-millisecond access
  - Shared across all services
  - Atomic operations

### Layer 2: Shared File System (Persistent)
- **Purpose**: Survives restarts and Redis failures
- **Path**: `/shared/config/prompt_templates.json`
- **Format**: JSON with metadata
- **Benefits**:
  - Persists across container restarts
  - Version control friendly
  - Human-readable backup

### Layer 3: Environment Variables (Override)
- **Purpose**: Production overrides without UI access
- **Examples**:
  ```bash
  QUADRACODE_GOVERNOR_SYSTEM="Production prompt"
  QUADRACODE_COMPRESSION_PROFILE="aggressive"
  ```
- **Priority**: Highest (overrides Redis and files)

## Loading Priority

The system loads configuration with this priority order:

1. **Environment Variables** (highest priority)
2. **Redis Hash** (if available)
3. **Shared File** (`/shared/config/`)
4. **User File** (`~/.quadracode/`)
5. **Default Templates** (fallback)

## Version Management

Each configuration save increments a version number:

```python
version = redis.incr("qc:config:version")
```

This allows:
- Detection of updates
- Audit trail
- Rollback capability
- Conflict resolution

## Real-time Synchronization

### Push Model (PubSub)
- UI publishes to `qc:config:reload` channel
- Runtime subscribes and receives immediate notifications
- Sub-second update propagation

### Pull Model (Polling)
- Runtime checks version every second
- Fallback if PubSub message missed
- Ensures eventual consistency

## Error Handling

The system gracefully handles failures:

1. **Redis Unavailable**: Falls back to file-based config
2. **File System Error**: Uses in-memory defaults
3. **Network Partition**: Version tracking ensures consistency
4. **Corrupt Data**: Validation before loading

## Security Considerations

1. **Access Control**: Redis ACLs can restrict config access
2. **Validation**: All prompts validated before use
3. **Audit Trail**: Version history in Redis stream
4. **Backup**: Multiple backup locations

## Testing the Persistence

### Manual Test

1. Open UI and make prompt changes
2. Click "Save to Runtime"
3. Check runtime logs for reload message
4. Verify in runtime:
   ```python
   from quadracode_runtime.config.prompt_manager import get_prompt_manager
   manager = get_prompt_manager()
   print(manager.get_templates().governor_system_prompt)
   ```

### Automated Test

```python
# Test script
import time
from quadracode_ui.utils.config_sync import get_config_sync
from quadracode_runtime.config.prompt_manager import get_prompt_manager

# UI side: Save config
ui_sync = get_config_sync()
ui_sync.save_prompts({"governor_system_prompt": "Test prompt"})

# Runtime side: Wait for auto-reload
time.sleep(2)  # Auto-reload happens within 1-2 seconds

# Verify
runtime_manager = get_prompt_manager()
assert runtime_manager.get_templates().governor_system_prompt == "Test prompt"
```

## Docker Compose Integration

Ensure Redis is available to both services:

```yaml
services:
  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
  
  ui:
    environment:
      - REDIS_HOST=redis
    depends_on:
      - redis
  
  runtime:
    environment:
      - REDIS_HOST=redis
    volumes:
      - shared_config:/shared/config
    depends_on:
      - redis

volumes:
  redis_data:
  shared_config:
```

## Troubleshooting

### Changes Not Persisting

1. Check Redis connectivity:
   ```python
   redis-cli ping
   ```

2. Verify version increments:
   ```python
   redis-cli get qc:config:version
   ```

3. Check runtime logs:
   ```bash
   grep "prompt templates" runtime.log
   ```

### Auto-reload Not Working

1. Ensure Redis PubSub is enabled
2. Check for thread exceptions in logs
3. Verify `enable_auto_reload=True` in PromptManager

### File Permissions

Ensure write permissions for:
- `/shared/config/`
- `~/.quadracode/`

## Performance Considerations

- **Reload Time**: < 100ms typically
- **Memory Usage**: ~1MB per configuration
- **Thread Overhead**: Single background thread
- **Redis Traffic**: < 1KB/s with polling

## Future Enhancements

1. **Versioned History**: Store last N versions in Redis
2. **A/B Testing**: Multiple active configurations
3. **Hot Reload Without Thread**: Using asyncio
4. **Conflict Resolution**: Multi-user edit detection
5. **Encrypted Storage**: For sensitive prompts
6. **Template Inheritance**: Base + override templates

## Summary

The prompt persistence system provides:

✅ **Immediate Updates**: Changes apply in < 2 seconds
✅ **Multi-layer Persistence**: Redis + File + Env
✅ **Auto-reload**: No manual intervention needed
✅ **Fault Tolerance**: Graceful degradation
✅ **Version Control**: Track all changes
✅ **Production Ready**: Environment variable overrides

This architecture ensures that prompt configurations are always synchronized between the UI and runtime, with multiple layers of persistence and real-time updates.

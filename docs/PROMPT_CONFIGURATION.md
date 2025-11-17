# Prompt Template Configuration Guide

## Overview

The Quadracode runtime now supports fully configurable prompt templates for all LLM interactions. This allows you to customize how the context engine manages compression, governance, and other operations without modifying code.

## Features

- **Configurable Prompts**: All LLM prompts are externalized and easily editable
- **Domain-Specific Templates**: Customize behavior for different content types (code, docs, tests)
- **Compression Profiles**: Multiple levels of compression aggressiveness
- **Adaptive Pressure**: Automatic adjustment based on context usage
- **UI Settings Page**: Interactive Streamlit interface for configuration
- **CLI Tool**: Command-line management of prompts
- **File-based Config**: JSON/YAML support for version control

## Quick Start

### 1. Using the UI

Navigate to the Streamlit app and select "⚙️ Prompt Settings" from the sidebar:

```bash
cd quadracode-ui
uv run streamlit run src/quadracode_ui/app.py
```

The UI provides tabs for:
- **Governor**: Context segment management prompts
- **Reducer**: Compression and summarization prompts
- **Compression**: Profile settings (conservative, balanced, aggressive, extreme)
- **Domains**: Domain-specific customizations
- **Export/Import**: Save and load configurations

### 2. Using the CLI

The prompt CLI tool provides command-line access to all prompt management features:

```bash
# List all available templates
uv run python -m quadracode_runtime.cli.prompt_cli list

# Show a specific template
uv run python -m quadracode_runtime.cli.prompt_cli show governor_system_prompt

# Update a template
uv run python -m quadracode_runtime.cli.prompt_cli update governor_system_prompt --save

# Test a template with variables
uv run python -m quadracode_runtime.cli.prompt_cli test reducer_chunk_prompt \
  --vars target_tokens=100 chunk="Sample text to summarize" \
  --domain code \
  --pressure 0.8

# Export current configuration
uv run python -m quadracode_runtime.cli.prompt_cli export my_prompts.json

# Import configuration
uv run python -m quadracode_runtime.cli.prompt_cli import my_prompts.json --save

# Validate all templates
uv run python -m quadracode_runtime.cli.prompt_cli validate

# Reset to defaults
uv run python -m quadracode_runtime.cli.prompt_cli reset --save
```

### 3. Environment Variables

You can override specific prompts using environment variables:

```bash
export QUADRACODE_GOVERNOR_SYSTEM="Your custom governor prompt"
export QUADRACODE_REDUCER_SYSTEM="Your custom reducer prompt"
export QUADRACODE_COMPRESSION_PROFILE="aggressive"
```

### 4. Configuration Files

Place a `prompt_templates.json` file in one of these locations:
- `~/.quadracode/prompt_templates.json` (user-specific)
- `/etc/quadracode/prompt_templates.json` (system-wide)
- `./prompt_templates.json` (project-specific)

Or specify a custom location:
```bash
export QUADRACODE_PROMPT_CONFIG=/path/to/your/prompts.json
```

## Template Variables

Templates support variable substitution using `{variable_name}` syntax:

### Governor Templates
- `{instructions}` - The task instructions
- `{payload}` - The context summary JSON

### Reducer Templates
- `{chunk}` - The text chunk to summarize
- `{target_tokens}` - Target token count
- `{focus}` - Optional focus area
- `{focus_clause}` - Formatted focus clause
- `{combined}` - Combined summaries to merge

### Domain Variables
- `{segment}` - Context segment content
- `{usage_ratio}` - Current context usage percentage
- `{current_types}` - List of current context types
- `{pending_tasks}` - List of pending tasks

## Compression Profiles

### Conservative (0.7 ratio)
- Preserves most details
- Maintains document structure
- Prioritizes recent information
- Best for critical operations

### Balanced (0.5 ratio)
- Standard compression level
- Good detail preservation
- Default profile

### Aggressive (0.3 ratio)
- Significant compression
- Key facts only
- Good for high context pressure

### Extreme (0.2 ratio)
- Maximum compression
- Only critical information
- Emergency context overflow

## Domain-Specific Templates

The system automatically detects content domains and applies specific guidance:

### Code Domain
- Focus: function signatures, logic flow, dependencies
- Style: preserve exact syntax
- Priority: implementation details

### Documentation Domain
- Focus: key concepts, examples, API references
- Style: maintain hierarchy
- Priority: usage patterns

### Test Results Domain
- Focus: failures, error messages, stack traces
- Style: structured statistics
- Priority: failing tests

### Tool Output Domain
- Focus: results, side effects, return values
- Style: concise data preservation
- Priority: successful operations and errors

## Adaptive Context Pressure

The system automatically adjusts compression based on context usage:

| Context Usage | Pressure Level | Behavior |
|--------------|----------------|----------|
| < 50% | Low | Thorough, preserve detail |
| 50-75% | Medium | Balance detail with conciseness |
| 75-90% | High | Aggressive compression |
| > 90% | Critical | Maximum compression |

## API Usage

### Python API

```python
from quadracode_runtime.config.prompt_manager import PromptManager

# Initialize manager
manager = PromptManager()

# Get templates
templates = manager.get_templates()

# Update a template
manager.update_template("governor_system_prompt", "New prompt text")

# Update compression profile
manager.update_compression_profile("custom", {
    "summary_ratio": 0.4,
    "preserve_detail": True
})

# Get effective prompt with enhancements
prompt = manager.get_effective_prompt(
    "reducer_chunk_prompt",
    context_ratio=0.8,  # High pressure
    domain="code",      # Code-specific
    chunk="...",        # Template variables
    target_tokens=100
)

# Save changes
manager.save_to_file()
```

### Integration with Context Engine

The context engine automatically uses configured prompts:

```python
from quadracode_runtime.config import ContextEngineConfig

config = ContextEngineConfig()
# config.prompt_templates is automatically loaded

# Access templates
prompts = config.prompt_templates

# Use in governor
system_prompt = prompts.governor_system_prompt
instructions = prompts.governor_instructions

# Apply compression profile
profile = prompts.get_compression_profile(config.compression_profile)

# Get domain-specific enhancements
domain_template = prompts.get_domain_template("code")
```

## Best Practices

1. **Version Control**: Keep prompt configurations in version control for team consistency
2. **Test Changes**: Use the CLI test command to preview prompt behavior
3. **Profile Selection**: Match compression profile to task criticality
4. **Domain Detection**: Let the system auto-detect domains when possible
5. **Pressure Monitoring**: Monitor context pressure to adjust profiles
6. **Incremental Changes**: Make small, tested changes to prompts
7. **Documentation**: Document custom prompts and their purposes

## Examples

### Custom Governor for Code-Heavy Projects

```json
{
  "governor_system_prompt": "You are a code-focused context governor. Prioritize implementation details, function signatures, and error handling. Maintain code coherence above all else.",
  "governor_instructions": "Analyze code segments. Preserve complete functions, compress comments aggressively. Output JSON with 'actions' and 'prompt_outline'."
}
```

### Aggressive Compression for Limited Context

```json
{
  "compression_profiles": {
    "ultra_compact": {
      "summary_ratio": 0.15,
      "preserve_detail": false,
      "prioritize_recent": true,
      "keep_structure": false
    }
  }
}
```

### Domain-Specific for ML Projects

```json
{
  "domain_templates": {
    "ml_training": {
      "focus": "hyperparameters, metrics, model architecture",
      "summary_style": "tabular with key metrics highlighted",
      "priority": "loss values, accuracy, convergence indicators"
    }
  }
}
```

## Troubleshooting

### Templates Not Loading
- Check file permissions on configuration files
- Verify JSON/YAML syntax is valid
- Look for error messages in logs

### Variable Substitution Errors
- Ensure all required variables are provided
- Check variable names match template placeholders
- Use the CLI test command to debug

### Compression Too Aggressive
- Switch to conservative profile
- Adjust summary_ratio in custom profile
- Reduce context_pressure thresholds

### Domain Not Detected
- Manually specify domain in API calls
- Add detection patterns to _detect_domain method
- Create custom domain templates

## Advanced Configuration

### Multi-Environment Setup

Create environment-specific configurations:

```bash
# Development
export QUADRACODE_PROMPT_CONFIG=~/.quadracode/prompts.dev.json

# Production
export QUADRACODE_PROMPT_CONFIG=~/.quadracode/prompts.prod.json

# Testing
export QUADRACODE_PROMPT_CONFIG=~/.quadracode/prompts.test.json
```

### Dynamic Profile Switching

```python
# Switch profiles based on context pressure
if context_ratio > 0.8:
    config.compression_profile = "aggressive"
elif context_ratio > 0.6:
    config.compression_profile = "balanced"
else:
    config.compression_profile = "conservative"
```

### Custom Pressure Thresholds

```python
# Override pressure modifiers
templates.pressure_modifiers = {
    "low": "Preserve maximum detail.",
    "medium": "Standard compression.",
    "high": "Compress aggressively.",
    "critical": "Emergency compression - absolute minimum only."
}
```

## Future Enhancements

Planned features for prompt configuration:

1. **A/B Testing**: Compare prompt effectiveness
2. **Analytics**: Track compression ratios and quality scores
3. **Auto-Tuning**: ML-based prompt optimization
4. **Template Library**: Shared community templates
5. **Hot Reload**: Apply changes without restart
6. **Prompt Versioning**: Track and rollback changes
7. **Performance Metrics**: Measure prompt impact on latency

## Support

For issues or questions about prompt configuration:

1. Check this documentation
2. Use the CLI validate command
3. Review example configurations in `/examples`
4. Check logs for error messages
5. Open an issue on GitHub

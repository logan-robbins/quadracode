# Rate Limiting Analysis - Quick Summary

## TL;DR: You're Good to Go! ✅

**Your Tier 4 Anthropic rate limits are more than sufficient for the test suite.**

- **Your Limits**: 4,000 requests/minute
- **Test Suite Usage**: ~20-30 requests/minute average
- **Conclusion**: You have **100-200x headroom** on rate limits

## Running the Tests

### Standard Run (Recommended)
```bash
uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO
```

The tests should run without any rate limit issues.

## If You Encounter Rate Limits (Unlikely)

### Symptom
- Tests timeout waiting for responses
- Orchestrator logs show `429` errors
- `rate_limit_error` messages from Anthropic

### Quick Fix
```bash
# Add 2-second cooldown between turns
E2E_TURN_COOLDOWN_SECONDS=2.0 uv run pytest tests/e2e_advanced -v
```

## What We Added

### 1. Rate Limiting Utilities
**File**: `tests/e2e_advanced/utils/timeouts.py`

New functions you can use in tests:
```python
from tests.e2e_advanced.utils.timeouts import rate_limit_sleep, RateLimiter

# Simple sleep with env var control
rate_limit_sleep(0.5, "between conversation turns")

# Advanced rate limiter
limiter = RateLimiter(max_requests_per_minute=50)
for turn in range(100):
    limiter.wait_if_needed()
    send_message(...)
```

### 2. Environment Variable Controls

| Variable | Default | Description |
|----------|---------|-------------|
| `E2E_TURN_COOLDOWN_SECONDS` | 0.5 | Seconds to wait between turns |
| `E2E_RATE_LIMIT_DISABLE` | - | Set to `1` to disable all rate limiting delays |

### 3. Documentation
- **RATE_LIMITING_ANALYSIS.md**: Detailed analysis of API usage patterns
- **README.md**: Updated with rate limiting guidance
- **RATE_LIMITING_SUMMARY.md**: This quick reference (you're reading it!)

## Models in Use

The test suite uses these Anthropic models:
- **Claude Sonnet 4** (Main driver): Orchestrator and agent decision-making
- **Claude Haiku 3** (Context reducer): Summarizing large tool outputs
- **Heuristic** (Governor): No API calls

## API Usage by Test

| Test | Duration | Turns | Est. API Calls/Min | Risk |
|------|----------|-------|-------------------|------|
| Foundation Long Run | 5-10 min | 30+ | 18-30 | ✅ Low |
| Context Engine | 10-15 min | 20+ | 10-15 | ✅ Low |
| PRP Autonomous | 15-20 min | Variable | 5-10 | ✅ Low |
| Fleet Management | 5-10 min | 10-20 | 15-25 | ⚠️ Medium |
| Workspace Integrity | 10-15 min | 15-25 | 10-20 | ✅ Low |
| Observability | 10-15 min | 20-30 | 15-25 | ✅ Low |

**Total Suite**: ~20-30 API calls/minute average across 60-90 minutes

## Why You're Safe

1. **Generous Limits**: Your 4,000 RPM limit is 100-200x higher than test usage
2. **Built-in Pacing**: Tests already have waits for processing (0.5s to 120s)
3. **Low Concurrency**: One test at a time, sequential execution
4. **Natural Throttling**: Orchestrator processing time creates natural gaps

## When to Add Rate Limiting

You should only add explicit rate limiting if:
1. You see `429` errors in logs (very unlikely)
2. You scale up to multiple parallel test runs
3. You reduce test turn delays significantly
4. You're on a lower API tier (Tier 1-3)

## Example: Running with Extra Caution

If you want to be extra cautious (not necessary):

```bash
# Conservative: 2s between turns, 2x timeouts
E2E_TURN_COOLDOWN_SECONDS=2.0 \
E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0 \
uv run pytest tests/e2e_advanced -m e2e_advanced -v
```

## Monitoring During Tests

Watch for rate limit indicators:
```bash
# Check orchestrator logs for 429 errors
docker compose logs -f orchestrator-runtime | grep -i "rate\|429"

# Check context metrics for API usage
docker compose exec redis redis-cli XRANGE qc:context:metrics - + | grep -i "reducer\|api"
```

## Support

If you encounter rate limiting issues:
1. Check `docker compose logs orchestrator-runtime` for 429 errors
2. Verify your API tier at https://console.anthropic.com/
3. Try `E2E_TURN_COOLDOWN_SECONDS=2.0` 
4. See `RATE_LIMITING_ANALYSIS.md` for detailed troubleshooting

---

**Bottom Line**: Your rate limits are excellent. Just run the tests normally. The infrastructure is ready if you ever need it.


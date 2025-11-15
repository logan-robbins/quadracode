# Rate Limiting Analysis for Advanced E2E Tests

## Current Configuration

### Models in Use
- **Primary Driver** (Orchestrator & Agent): `claude-sonnet-4-20250514`
- **Context Reducer**: `claude-3-haiku-20240307` 
- **Governor**: `heuristic` (no API calls)

### Your API Rate Limits (Tier 4)

| Model | Requests/Min | Input Tokens/Min | Output Tokens/Min |
|-------|-------------|------------------|-------------------|
| **Claude Sonnet 4.x** | 4,000 | 2M (≤200k context) | 400K (≤200k context) |
| **Claude Haiku 3.x** | 4,000 | 400K | 80K |

## Test Suite API Usage Analysis

### Test 1.1: Foundation Long Run (5-10 minutes)
- **Target**: 30+ turns over 5 minutes = ~6 turns/minute
- **API calls per turn**: 
  - 1 orchestrator call (Sonnet 4)
  - 0-2 agent calls (Sonnet 4)
  - 1-2 context reduction calls (Haiku 3)
  - **Total: ~3-5 API calls/turn** = **18-30 API calls/minute**
- **Current pacing**: 0.5s sleep between turns (line 173)
- **Risk**: **LOW** - Well within limits for single test

### Test 2.1: Context Engine Stress (10-15 minutes)
- **Target**: 20+ turns over 7 minutes = ~3 turns/minute
- **API calls per turn**: Similar to Foundation test
- **Total**: **~10-15 API calls/minute**
- **Current pacing**: None between turns, but lower turn rate
- **Risk**: **LOW** - Well within limits

### Test 3.1: PRP Autonomous (15-20 minutes)
- **Rejection cycles**: Variable, 1-3 cycles expected
- **API calls**: Multiple orchestrator + HumanClone interactions
- **Current pacing**: 60s and 120s waits built in (lines 323, 469)
- **Risk**: **LOW** - Long waits between cycles

### Test 4.x: Fleet Management (5-10 minutes)
- **Agent spawning**: 10-20 turns with agent lifecycle
- **API calls**: Orchestrator + multiple dynamic agents
- **Risk**: **MEDIUM** - Multiple agents could spike API usage

### Test 5.x: Workspace Integrity (10-15 minutes)
- **Turns**: 15-25 with workspace operations
- **API calls**: Standard orchestrator pattern
- **Risk**: **LOW**

### Test 6.x: Observability (10-15 minutes)
- **Turns**: 20-30 with time-travel logging
- **API calls**: Standard orchestrator pattern
- **Current pacing**: 5s sleeps after some operations (lines 458, 516)
- **Risk**: **LOW**

## Risk Assessment

### Individual Test Risk
✅ **LOW RISK** - Each individual test stays well under rate limits

### Full Suite Risk (Back-to-Back Execution)
⚠️ **MEDIUM RISK** - Running all tests consecutively could cause issues:

**Worst Case Scenario:**
- Total runtime: 60-90 minutes
- Average API calls: ~20-30/minute during active turns
- Peak during fleet management: Could spike to 40-50/minute
- **Risk**: Could approach **1,200-2,700 requests over 90 minutes** (~13-30 RPM average)
  
**This is still well within your 4K RPM limit**, but:
- **Token usage** could be a concern with large contexts
- **Burst traffic** during test transitions could trigger short-term limits

## Current Rate Limiting Mechanisms

### Existing Sleeps (Not for Rate Limiting)
```python
# Foundation test
time.sleep(0.5)  # Brief pause between turns (line 173)

# PRP test  
time.sleep(60)   # Wait for orchestrator work (line 323)
time.sleep(120)  # Wait for refinement cycle (line 469)

# Observability test
time.sleep(5)    # Wait for metrics (lines 458, 516)

# Fleet management
time.sleep(30)   # Wait for deletion (line 604)
```

**These are designed for waiting on processing, NOT rate limiting.**

## Recommendations

### 1. ✅ Current Approach is Adequate
**Your rate limits are generous enough that you likely won't hit issues** with the current test suite structure.

### 2. 🔧 Optional: Add Inter-Test Cooldown
Add a small delay between test modules to be extra safe:

```python
# In conftest.py
import time
import pytest

@pytest.fixture(scope="function", autouse=True)
def rate_limit_cooldown(request):
    """Add cooldown between tests to prevent API rate limiting."""
    yield
    # Small cooldown after each test completes
    if request.config.getoption("--rate-limit-cooldown"):
        cooldown = int(os.environ.get("E2E_RATE_LIMIT_COOLDOWN_SECONDS", "10"))
        time.sleep(cooldown)
```

Usage:
```bash
# Add 10s cooldown between tests
E2E_RATE_LIMIT_COOLDOWN_SECONDS=10 uv run pytest tests/e2e_advanced -m e2e_advanced -v
```

### 3. 🔧 Optional: Add Turn-Level Rate Limiting
For the high-turn-count tests, add configurable pacing:

```python
# In test_foundation_long_run.py (after line 173)
# Replace:
# time.sleep(0.5)  # Brief pause between turns

# With:
cooldown = float(os.environ.get("E2E_TURN_COOLDOWN_SECONDS", "0.5"))
time.sleep(cooldown)  # Brief pause between turns (rate limit protection)
```

Usage:
```bash
# Increase turn cooldown to 2 seconds
E2E_TURN_COOLDOWN_SECONDS=2.0 uv run pytest tests/e2e_advanced/test_foundation_long_run.py -v
```

### 4. 📊 Monitor Token Usage
The more concerning limit is **token throughput**, not request count.

**Risk areas:**
- Large tool outputs getting reduced
- Context engine operations with many segments
- Prompt caching (if enabled) counts toward input tokens

**Monitoring:**
```bash
# Check context metrics stream for token usage
docker compose exec redis redis-cli XRANGE qc:context:metrics - + | grep token_count
```

### 5. 🚨 If You Do Hit Rate Limits

**Symptoms:**
- Tests timeout waiting for responses
- Orchestrator logs show 429 errors
- Anthropic API errors in service logs

**Immediate fixes:**
```bash
# Option A: Increase turn cooldown
E2E_TURN_COOLDOWN_SECONDS=2.0 uv run pytest tests/e2e_advanced -v

# Option B: Increase timeout multiplier (gives more time for throttled requests)
E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0 uv run pytest tests/e2e_advanced -v

# Option C: Run tests individually with cooldown between
for test in tests/e2e_advanced/test_*.py; do
  uv run pytest "$test" -v
  sleep 30  # 30s cooldown between test files
done
```

## Conclusion

### ✅ You're Good to Run Now
Your Tier 4 rate limits are generous:
- 4,000 RPM >> ~30 RPM actual usage
- The existing sleeps provide natural rate limiting
- Test suite is designed with processing waits built-in

### 🎯 Recommendation: Run as-is
1. Start the full suite without modifications
2. Monitor the first test for any rate limit errors
3. If you see issues (unlikely), add the cooldown mechanisms above

### 📈 Future-Proofing
If you scale up testing (more parallel tests, tighter loops), consider:
- Setting `E2E_TURN_COOLDOWN_SECONDS=1.0` as default
- Adding inter-test cooldowns
- Using `E2E_ADVANCED_TIMEOUT_MULTIPLIER=1.5` in CI

---

**TLDR**: Your rate limits are sufficient. The test suite should run fine as-is. The existing sleeps provide natural pacing. Only add explicit rate limiting if you encounter 429 errors (unlikely).


# Quadracode Advanced E2E Testing Framework

This directory contains comprehensive, long-running end-to-end tests for the Quadracode multi-agent system.

## Overview

The advanced E2E testing framework validates:

- **False-Stop Detection**: HumanClone's ability to catch premature task completion
- **PRP Effectiveness**: Perpetual Refinement Protocol's recovery mechanisms
- **Multi-Agent Coordination**: Message routing, agent spawning, and fleet management
- **Context Engineering**: Progressive loading, curation, and quality management
- **Autonomous Mode**: Checkpoints, escalations, and final review processes
- **Workspace Integrity**: Isolation, snapshots, and drift detection
- **Observability**: Time-travel logging and metrics streams

## Directory Structure

```
tests/e2e_advanced/
├── utils/              # Reusable utilities
│   ├── logging_framework.py   # Test logging
│   ├── redis_helpers.py        # Redis stream utilities
│   ├── artifacts.py            # Artifact capture
│   ├── timeouts.py             # Polling and waiting
│   ├── agent_helpers.py        # Agent management
│   ├── metrics_collector.py    # Metrics collection
│   └── llm_judge.py            # LLM-as-a-judge classification
├── schemas/            # JSON schemas
│   └── metrics_schema.json     # Metrics validation schema
├── scripts/            # Analysis scripts
│   ├── aggregate_metrics.py    # Aggregate test metrics
│   ├── generate_metrics_report.py  # Generate markdown reports
│   └── plot_metrics.py         # Create visualizations
├── logs/               # Test execution logs (gitignored)
├── artifacts/          # Test artifacts (gitignored)
├── metrics/            # Individual test metrics (gitignored)
├── reports/            # Aggregate reports (gitignored)
├── plots/              # Generated visualizations (gitignored)
└── conftest.py         # Pytest fixtures
```

## Prerequisites

1. **Docker**: All tests run services in Docker Compose
2. **Environment Variables** (set in `.env` file - NO export needed):
   - `ANTHROPIC_API_KEY`: Required for real LLM calls (set in `.env` file in repo root)
   - `E2E_ADVANCED_TIMEOUT_MULTIPLIER`: Optional timeout multiplier (default: 1.0)
3. **Python Dependencies**:
   ```bash
   uv sync
   ```

## Running Tests

### Run All Advanced E2E Tests

```bash
uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO
```

### Run Specific Test Module

```bash
# Foundation tests (5-10 minutes)
uv run pytest tests/e2e_advanced/test_foundation_long_run.py -v

# Context engine stress tests (10-15 minutes)
uv run pytest tests/e2e_advanced/test_context_engine_stress.py -v

# PRP and autonomous mode tests (15-20 minutes)
uv run pytest tests/e2e_advanced/test_prp_autonomous.py -v

# Fleet management tests (5-10 minutes)
uv run pytest tests/e2e_advanced/test_fleet_management.py -v

# Workspace integrity tests (10-15 minutes)
uv run pytest tests/e2e_advanced/test_workspace_integrity.py -v

# Observability tests (10-15 minutes)
uv run pytest tests/e2e_advanced/test_observability.py -v
```

### Run with Increased Timeouts

For CI or slow environments (add to `.env` file or set inline):

```bash
# Option 1: Add to .env file
echo "E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0" >> .env
uv run pytest tests/e2e_advanced -v

# Option 2: Set inline (temporary override)
E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0 uv run pytest tests/e2e_advanced -v
```

### Run with Rate Limiting Protection

If you encounter API rate limits (e.g., Anthropic 429 errors), add turn cooldowns:

```bash
# Option 1: Add to .env file (recommended)
echo "E2E_TURN_COOLDOWN_SECONDS=1.0" >> .env  # 1 second cooldown
uv run pytest tests/e2e_advanced -v

# Option 2: Inline override for testing
E2E_TURN_COOLDOWN_SECONDS=2.0 uv run pytest tests/e2e_advanced -v  # 2 second cooldown

# Disable all rate limiting delays (faster tests, but may hit limits)
E2E_RATE_LIMIT_DISABLE=1 uv run pytest tests/e2e_advanced -v
```

See `RATE_LIMITING_ANALYSIS.md` for detailed analysis of API usage patterns.

## Metrics Collection

Tests automatically collect metrics including:

- **False-stops**: Count, rate, detection rate, recovery times
- **HumanClone effectiveness**: Precision, recall, F1 score, latency
- **PRP cycles**: State distribution, transition counts, novelty scores
- **Resource utilization**: Tokens, costs, tool calls, context events

Metrics are exported to `tests/e2e_advanced/metrics/{test_name}_{run_id}_metrics.json`.

## Generating Reports

### Aggregate Metrics

```bash
uv run python tests/e2e_advanced/scripts/aggregate_metrics.py \
  --input "tests/e2e_advanced/metrics/*.json" \
  --output tests/e2e_advanced/reports/aggregate_report.json \
  --csv tests/e2e_advanced/reports/aggregate_report.csv \
  --validate
```

### Generate Markdown Report

```bash
uv run python tests/e2e_advanced/scripts/generate_metrics_report.py \
  --aggregate tests/e2e_advanced/reports/aggregate_report.json \
  --output tests/e2e_advanced/reports/summary_report.md
```

### Generate Visualizations

```bash
uv run python tests/e2e_advanced/scripts/plot_metrics.py \
  --aggregate tests/e2e_advanced/reports/aggregate_report.json \
  --output tests/e2e_advanced/plots/
```

## Test Duration

Expected durations for test modules:

| Module | Duration | Turns | Services |
|--------|----------|-------|----------|
| Foundation | 5-10 min | 30-50 | Base stack |
| Context Engine | 10-15 min | 20-30 | Base stack |
| PRP/Autonomous | 15-20 min | Variable | + HumanClone |
| Fleet Management | 5-10 min | 10-20 | + Dynamic agents |
| Workspace | 10-15 min | 15-25 | Base stack |
| Observability | 10-15 min | 20-30 | Base stack |

**Total suite runtime:** ~60-90 minutes

## Troubleshooting

### Test Timeout

**Symptom**: Test fails with `TimeoutError`

**Solutions**:
- Increase timeout multiplier: `E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0`
- Check Docker resource allocation (CPU, memory)
- Verify API rate limits not exceeded (see below)

### API Rate Limiting (429 Errors)

**Symptom**: Orchestrator logs show `429` errors or `rate_limit_error` from Anthropic API

**Solutions**:
- Add turn cooldown: `E2E_TURN_COOLDOWN_SECONDS=2.0 uv run pytest tests/e2e_advanced -v`
- Run tests individually with delays between files
- Check your Anthropic API tier and limits at https://console.anthropic.com/
- See `RATE_LIMITING_ANALYSIS.md` for detailed guidance

**Note**: With Tier 4 limits (4000 RPM), rate limiting should be rare. The test suite averages ~20-30 RPM.

### Redis Stream Gaps

**Symptom**: Assertion fails on stream monotonicity

**Solutions**:
- Check service logs: `docker compose logs orchestrator-runtime`
- Verify Redis persistence: `docker compose ps redis`
- Restart stack: `docker compose down -v && docker compose up -d`

### Agent Spawn Failures

**Symptom**: Agent never registers or becomes healthy

**Solutions**:
- Check Docker daemon: `docker ps`
- Verify network: `docker network ls`
- Check agent logs: `docker compose logs agent-runtime`
- Rebuild images: `docker compose build agent-runtime`

### HumanClone Not Rejecting

**Symptom**: PRP test fails because HumanClone accepts false-stop

**Solutions**:
- Verify `QUADRACODE_SUPERVISOR_RECIPIENT=human_clone` set
- Check HumanClone prompt configuration
- Review HumanClone logs: `docker compose logs human-clone-runtime`
- Ensure task actually has failures (test setup correct)

## Debug Commands

```bash
# Check service health
docker compose ps

# View live orchestrator logs
docker compose logs -f orchestrator-runtime

# Inspect Redis streams
docker compose exec redis redis-cli XLEN qc:mailbox/orchestrator

# Check agent registry
curl http://localhost:8090/agents | jq

# List workspace containers
docker ps | grep quadracode-workspace

# Inspect metrics stream
docker compose exec redis redis-cli XRANGE qc:context:metrics - + COUNT 10
```

## CI Integration

To run in CI pipeline:

```yaml
- name: Setup Environment
  run: |
    # Create .env file with secrets
    echo "ANTHROPIC_API_KEY=${{ secrets.ANTHROPIC_API_KEY }}" > .env
    echo "E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0" >> .env

- name: Run Advanced E2E Tests  
  run: |
    # Tests automatically use .env file
    uv run pytest tests/e2e_advanced -v --junitxml=test-results.xml
  timeout-minutes: 120

- name: Upload Test Artifacts
  if: always()
  uses: actions/upload-artifact@v3
  with:
    name: e2e-advanced-artifacts
    path: |
      tests/e2e_advanced/logs/
      tests/e2e_advanced/metrics/
      tests/e2e_advanced/reports/
      tests/e2e_advanced/plots/
```

## Contributing

When adding new tests:

1. Follow existing patterns in `conftest.py` fixtures
2. Use `MetricsCollector` to track relevant metrics
3. Log all turns and tool calls for debugging
4. Include detailed assertion messages for failures
5. Document expected duration and prerequisites
6. Add entry to this README

## Reference

- Main plan: `PLAN.md` (repository root)
- Technical implementation: `TECHNICAL_IMPLEMENTATION.md`
- Base E2E tests: `tests/test_end_to_end.py`


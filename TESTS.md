# Testing Standards

All Quadracode tests are **full end-to-end checks** that exercise the production stack. There are no lightweight unit tests or mocked integrations in this repository.

## Core Rules

- **Always run the real platform.** Every test must start the Docker Compose services (`redis`, `redis-mcp`, `agent-registry`, `orchestrator-runtime`, `agent-runtime`, and any dependencies). If the stack is not already running, start it with `docker compose up -d ...` and confirm health via `docker compose ps --services --filter "status=running"`.
- **Real model traffic is mandatory.** Configure valid API keys in `.env` (e.g. `ANTHROPIC_API_KEY`) before running the suite. Tests fail immediately if required credentials are missing.
- **No mocks or stubs.** Interactions must go through Redis Streams, LangGraph runtimes, agent registry, workspace tooling, and MCP adapters exactly as production would.
- **Create and tear down real workloads.** Each test sends genuine chat messages, triggers tool executions, provisions workspaces/containers, and verifies cleanup.
- **Fail fast on missing prerequisites.** Lack of Docker, Compose, or API keys is a test failure, not a skip.

## Running the Suite

You can run tests in two ways: from the host machine or inside a Docker container.

### Option A: Running Tests from Host Machine (Traditional)

1. Export all required environment variables (see `.env` template).
2. Ensure Docker Engine and the Compose plugin are installed and running.
3. Start or verify the docker-compose stack:
   ```bash
   docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime
   docker compose ps --services --filter "status=running"
   ```
4. From the repo root, execute:
   ```bash
   # Quick smoke tests (infrastructure validation, <5 minutes)
   uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v
   
   # Full comprehensive suite (60-90 minutes)
   uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO
   ```

### Option B: Running Tests Inside Docker Network (Recommended)

This approach ensures tests use the same DNS names and network as the services.

1. Ensure your `.env` and `.env.docker` files are configured with API keys.
2. Start the full stack including the test runner:
   ```bash
   docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime test-runner
   docker compose ps --services --filter "status=running"
   ```
3. Execute tests inside the test-runner container:
   ```bash
   # Quick smoke tests
   docker compose exec test-runner uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v
   
   # Full comprehensive suite
   docker compose exec test-runner uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO
   ```

The test runner container automatically detects it's running inside Docker and uses internal service names (redis, agent-registry, redis-mcp) instead of localhost.

## Advanced E2E Tests

The `tests/e2e_advanced/` directory contains long-running, comprehensive end-to-end tests designed to validate:

- **False-Stop Detection**: HumanClone's ability to catch premature task completion
- **Perpetual Refinement Protocol (PRP)**: Recovery mechanisms and refinement cycles
- **Context Engineering**: Progressive loading, curation, and quality management
- **Autonomous Mode**: Checkpoints, escalations, and final review processes
- **Fleet Management**: Agent spawning, deletion, and hotpath protection
- **Workspace Integrity**: Isolation, snapshots, and drift detection
- **Observability**: Time-travel logging and metrics streams

### Test Modules

| Module | Tests | Duration | Description |
|--------|-------|----------|-------------|
| `test_foundation_long_run.py` | 2 | 5-10 min | Sustained message flows and multi-agent routing |
| `test_context_engine_stress.py` | 2 | 10-15 min | Progressive loading and context curation |
| `test_prp_autonomous.py` | 2 | 15-20 min | HumanClone rejection cycles and autonomous execution |
| `test_fleet_management.py` | 2 | 5-10 min | Dynamic agent lifecycle and hotpath protection |
| `test_workspace_integrity.py` | 2 | 10-15 min | Multi-workspace isolation and integrity snapshots |
| `test_observability.py` | 2 | 10-15 min | Time-travel logs and metrics stream coverage |

**Total suite runtime:** ~60-90 minutes

### Running Advanced E2E Tests

```bash
# Run all advanced E2E tests
uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO

# Run specific module
uv run pytest tests/e2e_advanced/test_prp_autonomous.py -v

# Run with increased timeouts for CI or slow environments
E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0 uv run pytest tests/e2e_advanced -v
```

### Prerequisites

- **Environment Variables**: `ANTHROPIC_API_KEY` must be set
- **Optional Configuration**: 
  - `QUADRACODE_TIME_TRAVEL_ENABLED=true` for observability tests
  - `QUADRACODE_MODE=autonomous` for autonomous mode tests
  - `SUPERVISOR_RECIPIENT=human_clone` for PRP tests

### Metrics Collection

Tests automatically collect comprehensive metrics including:

- False-stop counts, rates, and detection effectiveness
- HumanClone precision, recall, and F1 scores
- PRP cycle counts and state distributions
- Resource utilization (tokens, costs, tool calls)

Metrics are exported to `tests/e2e_advanced/metrics/` as JSON files. Use the reporting scripts to generate aggregate reports:

```bash
# Aggregate metrics from multiple test runs
uv run python tests/e2e_advanced/scripts/aggregate_metrics.py \
  --input "tests/e2e_advanced/metrics/*.json" \
  --output tests/e2e_advanced/reports/aggregate_report.json

# Generate markdown report
uv run python tests/e2e_advanced/scripts/generate_metrics_report.py \
  --aggregate tests/e2e_advanced/reports/aggregate_report.json \
  --output tests/e2e_advanced/reports/summary_report.md

# Create visualizations
uv run python tests/e2e_advanced/scripts/plot_metrics.py \
  --aggregate tests/e2e_advanced/reports/aggregate_report.json \
  --output tests/e2e_advanced/plots/
```

For detailed documentation, see `tests/e2e_advanced/README.md`.

## Adding New Tests

- All new E2E scenarios belong under `tests/e2e_advanced/`.
- Infrastructure smoke tests (still require the docker-compose stack) belong in `tests/e2e_advanced/test_foundation_smoke.py`.
- Full-stack integration tests belong in the appropriate test module based on the feature area.
- Reuse the helper utilities in `tests/e2e_advanced/utils/` (Redis helpers, agent management, metrics collection, etc.).
- Every new test should:
  1. Use the `docker_stack` fixture (or validate prerequisites for smoke tests).
  2. Drive an observable workflow (sending messages, invoking tools, spawning agents, etc.).
  3. Assert on real outputs (Redis streams, registry responses, container mounts, metrics).
  4. Collect logs and metrics for post-test analysis.
  5. Use the advanced E2E utilities for timeouts, polling, and artifact capture.

Any change to testing must keep these guarantees intact. If a faster signal is needed, add to `test_foundation_smoke.py`, but do **not** relax the full-stack coverage in the comprehensive suite.

# Testing Standards

All Quadracode tests are **full end-to-end checks** that exercise the production stack. There are no lightweight unit tests or mocked integrations in this repository.

## Core Rules

- **Always run the real platform.** Every test must start the Docker Compose services (`redis`, `redis-mcp`, `agent-registry`, `orchestrator-runtime`, `agent-runtime`, and any dependencies). If the stack is not already running, start it with `docker compose up -d ...` and confirm health via `docker compose ps --services --filter "status=running"`.
- **Real model traffic is mandatory.** Configure valid API keys in `.env` (e.g. `ANTHROPIC_API_KEY`) before running the suite. Tests fail immediately if required credentials are missing.
- **No mocks or stubs.** Interactions must go through Redis Streams, LangGraph runtimes, agent registry, workspace tooling, and MCP adapters exactly as production would.
- **Create and tear down real workloads.** Each test sends genuine chat messages, triggers tool executions, provisions workspaces/containers, and verifies cleanup.
- **Fail fast on missing prerequisites.** Lack of Docker, Compose, or API keys is a test failure, not a skip.

## QUICK START FOR AI AGENTS

### EASIEST METHOD: Interactive Test Runner

```bash
# Run this single script - it handles EVERYTHING
./run_e2e_tests_for_ai.sh

# The script will:
# - Check Docker is running
# - Verify API keys
# - Start all services
# - Wait for health checks
# - Give you a menu to choose tests
# - Run tests and report results
```

### MANUAL METHOD: Direct Commands

To run the full E2E integration tests with live LLM calls:

```bash
# Step 1: Ensure API keys are configured
# Check that .env and .env.docker have ANTHROPIC_API_KEY set
cat .env | grep ANTHROPIC_API_KEY  # Should show your key
cat .env.docker | grep ANTHROPIC_API_KEY  # Should show your key

# Step 2: Start the complete Docker stack
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime test-runner

# Step 3: Verify all services are healthy (should show 6 running services)
docker compose ps --services --filter "status=running" | wc -l  # Should output: 6

# Step 4: Run individual test modules (recommended for step-by-step debugging)
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh foundation_smoke"
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh prp_autonomous"
```

**Expected behavior:**
- Tests will make real API calls to Anthropic Claude
- Tests will spawn/delete Docker containers dynamically
- Full suite takes 60-90 minutes
- Individual modules take 5-20 minutes each

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

## Running Tests Individually for Debugging

For debugging and development, you can run test modules one at a time using the test runner:

### Using the Test Runner Script

**From host machine:**
```bash
cd tests/e2e_advanced

# List all available test modules
./run_individual_tests.sh --list

# Run a specific module
./run_individual_tests.sh foundation_smoke
./run_individual_tests.sh prp_autonomous

# Run with verbose output for debugging
./run_individual_tests.sh -v context_engine_stress

# Run with debug pauses between test functions
./run_individual_tests.sh -d fleet_management

# Run all modules in sequence, stopping at first failure
./run_individual_tests.sh all --stop-on-fail
```

**Inside Docker container (Recommended for consistent environment):**

The test-runner container includes:
- Full project with all dependencies installed via `uv sync`
- Environment variables from `.env` and `.env.docker` (including API keys)
- Internal Docker DNS names (redis, agent-registry, redis-mcp)
- Test runner scripts at `/app/tests/e2e_advanced/`

```bash
# Build and start test runner container
docker compose up -d --build test-runner

# Verify the container setup (optional)
./tests/verify_test_runner.sh

# Run individual tests inside container
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh foundation_smoke"
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh -v prp_autonomous"
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh all --stop-on-fail"

# Or enter the container interactively
docker compose exec -it test-runner bash
cd /app/tests/e2e_advanced
./run_individual_tests.sh --list
./run_individual_tests.sh foundation_smoke
```

### Using the Python Test Runner

```bash
cd tests/e2e_advanced

# List available modules
uv run python run_tests.py --list

# Run specific module
uv run python run_tests.py foundation_smoke

# Run with verbose output
uv run python run_tests.py foundation_smoke --verbose

# Run all modules
uv run python run_tests.py all
```

The test runner creates detailed logs in `tests/e2e_advanced/logs/` for each run, including:
- Full output capture
- JUnit XML reports
- HTML reports
- Execution summaries

## Advanced E2E Test Modules

### CRITICAL FOR AI AGENTS: Test Module Details

Each test module performs REAL operations with the live Docker stack:

| Module | What It Actually Does | Duration | Real Operations |
|--------|----------------------|----------|-----------------|
| `test_foundation_smoke.py` | Validates test infrastructure WITHOUT LLM calls | 2-5 min | - Checks Redis connectivity<br>- Verifies agent registry<br>- Tests logging framework<br>- No API costs |
| `test_foundation_long_run.py` | Sustained orchestrator-agent communication | 5-10 min | - Sends 50+ messages via Redis<br>- Real LLM calls to Claude<br>- Multi-agent routing tests |
| `test_context_engine_stress.py` | Tests context management under load | 10-15 min | - Progressive context loading<br>- Memory curation with LLM<br>- Tests token limits |
| `test_prp_autonomous.py` | HumanClone rejection & refinement cycles | 15-20 min | - Multiple LLM rejection cycles<br>- Tests autonomous recovery<br>- Most API-intensive test |
| `test_fleet_management.py` | Dynamic agent spawning/deletion | 5-10 min | - Creates/destroys Docker containers<br>- Tests orchestrator scaling<br>- Registry health checks |
| `test_workspace_integrity.py` | Multi-workspace isolation testing | 10-15 min | - Creates multiple workspaces<br>- Tests snapshot/restore<br>- File system operations |
| `test_observability.py` | Logging and metrics validation | 10-15 min | - Time-travel log capture<br>- Metrics stream validation<br>- Event correlation |

**Total suite runtime:** ~60-90 minutes  
**Expected API calls:** 500-1000 to Anthropic Claude  
**Docker operations:** Spawns/deletes 10+ containers dynamically

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

## Troubleshooting Guide for AI Agents

### Quick Diagnostic Commands

```bash
# Check if Docker is running
docker version > /dev/null 2>&1 && echo "✓ Docker is running" || echo "✗ Docker not running"

# Check if services are up (should show 6)
docker compose ps --services --filter "status=running" | wc -l

# Check if API key is set
grep -q "ANTHROPIC_API_KEY=sk-" .env && echo "✓ API key in .env" || echo "✗ Missing API key in .env"
grep -q "ANTHROPIC_API_KEY=sk-" .env.docker && echo "✓ API key in .env.docker" || echo "✗ Missing API key in .env.docker"

# Check Redis connectivity
docker compose exec -T redis redis-cli PING  # Should output: PONG

# Check agent registry
curl -s http://localhost:8090/health | grep -q "ok" && echo "✓ Registry healthy" || echo "✗ Registry not responding"
```

### Common Test Failures & Solutions

| Error Message | Likely Cause | Solution |
|--------------|-------------|----------|
| `ANTHROPIC_API_KEY not set` | Missing API key | Add valid key to `.env` and `.env.docker` |
| `Connection refused` | Services not running | Run `docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime test-runner` |
| `TimeoutError` in tests | Services not healthy | Wait 30s after starting stack, check `docker compose ps` |
| `redis.exceptions.ConnectionError` | Redis not accessible | Ensure running inside test-runner container or correct host configured |
| `404 from agent-registry` | Registry not ready | Wait for health check: `docker compose ps agent-registry` should show "healthy" |
| `Container not found` | Docker socket issue | Ensure `/var/run/docker.sock` is mounted (check docker-compose.yml) |

### Test Execution Checklist

- [ ] Docker Desktop/Engine is running
- [ ] `.env` file exists with valid `ANTHROPIC_API_KEY`
- [ ] `.env.docker` file exists with valid `ANTHROPIC_API_KEY`
- [ ] All 6 services are running: `docker compose ps --services --filter "status=running" | wc -l` shows `6`
- [ ] Redis responds: `docker compose exec -T redis redis-cli PING` returns `PONG`
- [ ] Agent registry healthy: `curl http://localhost:8090/health` returns `{"status":"ok"}`
- [ ] Test runner container is running: `docker compose ps test-runner` shows "running"

### Emergency Reset

If tests are failing unexpectedly:

```bash
# Complete reset (DESTRUCTIVE - removes all data)
docker compose down -v
docker system prune -af
docker volume prune -f

# Rebuild and restart everything
docker compose build --no-cache
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime test-runner

# Wait for services
sleep 30

# Verify health
docker compose ps
docker compose exec -T redis redis-cli PING

# Retry tests
docker compose exec test-runner bash -c "cd /app && uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v"
```

# Testing Standards

All Quadracode tests are **full end-to-end checks** that exercise the production stack. There are no lightweight unit tests or mocked integrations in this repository.

## Core Rules

- **Always run the real platform.** Every test must start the Docker Compose services (`redis`, `redis-mcp`, `agent-registry`, `orchestrator-runtime`, `agent-runtime`, and any dependencies). Tests may build images when required.
- **Real model traffic is mandatory.** Configure valid API keys in `.env` (e.g. `ANTHROPIC_API_KEY`) before running the suite. Tests fail immediately if required credentials are missing.
- **No mocks or stubs.** Interactions must go through Redis Streams, LangGraph runtimes, agent registry, workspace tooling, and MCP adapters exactly as production would.
- **Create and tear down real workloads.** Each test sends genuine chat messages, triggers tool executions, provisions workspaces/containers, and verifies cleanup.
- **Fail fast on missing prerequisites.** Lack of Docker, Compose, or API keys is a test failure, not a skip.

## Running the Suite

1. Export all required environment variables (see `.env` template).
2. Ensure Docker Engine and the Compose plugin are installed and running.
3. From the repo root, execute:
   ```bash
   uv run pytest tests/e2e -m e2e -q
   ```
   Individual targets (e.g. `tests/e2e/test_end_to_end.py`) are acceptable but must still satisfy the rules above.

## Adding New Tests

- New scenarios belong under `tests/e2e/`.
- Reuse the helper utilities in `tests/e2e/test_end_to_end.py` (compose orchestration, Redis helpers, etc.).
- Every new test should:
  1. Validate prerequisites (`require_prerequisites()`).
  2. Bring up the compose stack (or reuse a running stack when coordinated across the suite).
  3. Drive an observable workflow (sending messages, invoking tools, spawning agents, etc.).
  4. Assert on real outputs (Redis streams, registry responses, container mounts, metrics).
  5. Collect logs and tear the stack down in a `finally` block.

Any change to testing must keep these guarantees intact. If a faster signal is needed, introduce a new script or CLI command, but do **not** relax the full-stack coverage in this suite.

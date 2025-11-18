# Quadracode

**An always-on, LangGraph-native orchestration platform for asynchronous, long-running AI workloads with dynamic agent fleet management.**

Quadracode is a production-ready framework that enables AI agents to handle complex, multi-step tasks that span minutes, hours, or days—without blocking, without losing state, and without manual intervention. Built on Redis Streams, LangGraph checkpointing, and MCP-aware tooling, Quadracode provides the infrastructure you need to deploy resilient, self-scaling AI agent systems.

## Why Quadracode?

Most AI agent frameworks are designed for synchronous, short-lived interactions. Quadracode is purpose-built for **real-world automation** where:

- **Tasks take time**: Code reviews, data analysis, multi-service deployments, research synthesis
- **Failures happen**: Network issues, rate limits, service restarts—your work should survive them
- **Delegation is essential**: One orchestrator coordinates multiple specialized agents, each with their own tools and responsibilities
- **Scale matters**: The orchestrator dynamically spawns and terminates agents based on workload demands
- **Observability matters**: Every decision, tool call, and message is traced and inspectable

### Key Features

- **Persistent State**: LangGraph checkpoints keyed by conversation ID survive process restarts and resume exactly where they left off
- **Async-First**: Orchestrator never blocks on agent work; long-running jobs emit incremental updates over Redis Streams
- **Multi-Agent Coordination**: Built-in service registry, dynamic routing, and agent health tracking
- **Dynamic Fleet Management**: Orchestrator autonomously spawns and deletes agents based on workload, creating specialized agents for complex tasks
- **Two-Level Autonomy Loop**: A `HumanClone` agent acts as a relentless, skeptical reviewer for the orchestrator, ensuring that work is never prematurely considered "done."
- **MCP Integration**: Standardized tool interfaces via Model Context Protocol for seamless agent capability sharing
- **Context Engineering Node**: Progressive loader, prioritised compression, LLM-backed summarisation, and Redis-backed metrics keep long-running chats sharp without losing history
- **Full Observability**: Streamlit control plane with:
  - Real-time chat with 10-second background polling
  - Advanced Redis Streams monitoring with time range and regex filtering
  - Hierarchical workspace file browser with syntax highlighting
  - Interactive Plotly dashboards for metrics visualization
  - Agent activity drill-down and event timeline
- **Workspace Isolation**: Docker-based sandboxed environments with volume persistence for agent file operations
- **Workspace Integrity Management**: HumanClone rejections and exhaustion events trigger deterministic snapshots, diffable manifests, and checksum validation with automatic restoration when drift is detected
- **Platform Agnostic**: Runs on Docker Compose or Kubernetes with the same codebase
- **Production Ready**: Comprehensive E2E tests, structured message contracts, fault-tolerant design

#### HUMAN_OBSOLETE Autonomy Loop

```
┌──────────────────────────────────────────────────────────────────┐
│ Goal Intake                                                       │
│ - Human seeds task + guardrails via Streamlit sidebar             │
├──────────────────────────────────────────────────────────────────┤
│ Outer Loop: Propose → Reject → Refine                             │
│ ┌────────────────────────────┐        ┌────────────────────────┐ │
│ │ Orchestrator Runtime       │◄──────►│ HumanClone (Reviewer)  │ │
│ │ (Proposes final solution)  │        │ (Relentlessly rejects) │ │
│ └─────────────┬──────────────┘        └────────────────────────┘ │
│               │                                                  │
│ Inner Loop: Evaluate → Critique → Plan → Execute                  │
│               ▼                                                  │
│ ┌────────────────────────────┐        ┌────────────────────────┐ │
│ │ Dynamic Agent Fleet        │        │ Redis Streams          │ │
│ │ - Specialized workers      │        │ - qc:mailbox/*         │ │
│ │ - Agent registry health    │        │ - - qc:autonomous:events │ │
│ │ - MCP tool discovery       │        └────────┬───────────────┘ │
│ └─────────────┬──────────────┘                 │                 │
│               │                                 │                 │
│               ▼                                 │                 │
│ ┌────────────────────────────┐        ┌─────────┴──────────────┐ │
│ │ UI / Control Plane         │◄──────►│ Observability & Logs   │ │
│ │ - Chat + autonomous tab    │        │ - Dashboard panels     │ │
│ │ - Guardrail settings       │        │ - Redis/metrics tail   │ │
│ │ - Emergency stop control   │        │ - Research exports     │ │
│ └────────────────────────────┘        └────────────────────────┘ │
│                                                                  │
│ Escalate only on fatal errors or human-triggered emergency stop. │
└──────────────────────────────────────────────────────────────────┘
```

The autonomy of the system is maintained by a two-level loop:

1.  **The Inner Loop (Orchestrator-Agent):** This is the "Evaluate → Critique → Plan → Execute" loop. The orchestrator delegates tasks to the agent fleet, critiques their work, and plans the next steps.

2.  **The Outer Loop (Orchestrator-HumanClone):** When the orchestrator believes it has completed the entire task, it enters the outer loop. It submits its final work product to the `HumanClone` for review. The `HumanClone`, with its relentlessly skeptical prompt, will almost always reject the work and send it back to the orchestrator with a generic exhortation to "go deeper" or "try again." This forces the orchestrator to begin its inner loop anew, finding new ways to improve its work.

This two-level loop ensures that the system is always questioning its own conclusions and is constantly striving to produce a better result. The only escape from this loop is via the `escalate_to_human` tool, which the orchestrator is programmed to use only in cases of truly unrecoverable error.

## Repository Layout

This monorepo contains several Python 3.12 packages and supporting assets:

- `quadracode-runtime/` — Shared runtime for all services. Contains the LangGraph workflow (driver + tools), the Context Engineering node (pre/governor/post/tool_response), Redis/MCP messaging, metrics emitters, and state/contracts glue.
- `quadracode-orchestrator/` — Orchestrator service wrapper. Provides system prompt, runtime profile, and process entrypoint to run the orchestrator graph.
- `quadracode-agent/` — Generic agent service wrapper. Uses the shared runtime with the agent profile to execute delegated work.
- `quadracode-agent-registry/` — Uvicorn/FastAPI registry on port 8090 for agent discovery, health, and stats used by the orchestrator.
- `quadracode-tools/` — Reusable tools exposed to agents (LangChain and MCP-backed) such as `agent_management`, workspace management, file IO, etc.
- `quadracode-contracts/` — Shared Pydantic models and message envelope contracts used across services.
- `quadracode-ui/` — Streamlit UI with 5 pages: Chat (background polling), Mailbox Monitor (advanced filtering), Workspaces (hierarchical tree), Dashboard (Plotly charts), and Prompt Settings.
- `Dockerfile.workspace` — Docker image for sandboxed workspace containers (`quadracode-workspace:latest`)
- `scripts/` — Agent management helpers for Docker/Kubernetes and stream tailing utilities.
- `tests/e2e_advanced/` — Comprehensive long-running E2E test suite with smoke tests, metrics collection, and reporting

## Always-On Philosophy

Quadracode is built for Always-On AI: agents and orchestrators maintain progress across long-running, multi-step work.

- Durable state via LangGraph checkpointing and Redis streams
- Non-blocking orchestration and incremental updates for long jobs
- Dynamic fleet management to scale capacity up/down as needed
- Context Engineering keeps history sharp via curation, reduction, and progressive loading

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                     Streamlit UI (port 8501)                     │
│  💬 Chat  |  📡 Mailbox Monitor  |  📁 Workspaces  |  📊 Dashboard │
└────────────────────────┬─────────────────────────────────────────┘
                         │ (background polling)
                         ▼
              ┌──────────────────┐
              │  Redis Streams   │
              │  (Event Fabric)  │
              └─────────┬────────┘
                        │
       ┌────────────────┼────────────────┐
       ▼                ▼                ▼
┌─────────────┐  ┌─────────────┐  ┌──────────────┐
│Orchestrator │  │Agent Runtime│  │HumanClone    │
│  Runtime    │  │             │  │  Runtime     │
└──────┬──────┘  └─────────────┘  └──────────────┘
       │
       │ spawns/manages via Docker socket
       ▼
┌──────────────────────────────────────────┐
│     Dynamic Fleet & Workspace Engine     │
│  ┌───────────┐  ┌────────────────────┐  │
│  │  Agents   │  │ Workspace Volumes  │  │
│  │ (on-demand│  │ (persistent files) │  │
│  └───────────┘  └────────────────────┘  │
└──────────┬───────────────────────────────┘
           │
           ▼
    ┌─────────────┐
    │   Agent     │
    │  Registry   │
    │  (FastAPI)  │
    └─────────────┘
```

### Core Components

- **Redis Streams** (`qc:mailbox/<recipient>`) provide durable, ordered mailboxes for every participant
- **LangGraph runtimes** consume their mailbox, execute stateful graphs, and publish responses back onto the fabric
- **Agent Registry** (FastAPI on port `8090`) tracks agent identities, health, and ports for dynamic routing
- **Agent Management** scripts and tools enable the orchestrator to spawn/delete agents autonomously based on workload
- **Workspace Engine** (Docker-based) provides isolated containers with mounted volumes for agent file operations
- **Redis-MCP proxy** exposes the Redis transport to MCP-compatible clients for runtime tool loading
- **Streamlit UI** (port `8501`) provides comprehensive control plane:
  - Real-time chat with background polling
  - Advanced mailbox monitoring with filtering
  - Hierarchical workspace file browser
  - Interactive Plotly dashboards with metrics
- **Context Metrics Stream** (`qc:context:metrics`) records every `pre_process`, `post_process`, `tool_response`, `curation`, `load`, `externalize`, and `governor_plan` event so you can audit what the context engine did turn-by-turn

Every message is a simple envelope: `timestamp`, `sender`, `recipient`, `message`, and `payload` (JSON). The payload carries threading metadata, correlation IDs, tool outputs, and routing hints.

### Context Engineering Node

Every orchestrator turn traverses an opt-in context engineering pipeline:

1. **`context_pre`** scores the active state, runs the MemAct curator when quality dips or the window overflows, and pushes just-in-time context via the progressive loader.
2. **`context_governor`** (LLM-backed) reviews the latest state, plans retain/compress/summarize/externalize/discard operations, and hands the driver a goal-aware prompt outline.
3. **`driver` / tools** execute with the trimmed, skill-aware context; tool responses flow back into the working set automatically.
4. **`context_post`** reflects on the turn, updates the evolving playbook, appends curation rules, and checkpoints if needed.
5. **`context_tool`** captures each tool output, evaluates relevance, and—when payloads are large—invokes the reducer to summarise them.

The reducer uses Anthropic’s Claude Haiku by default (configurable via `ContextEngineConfig.reducer_model`) to map/reduce long blobs into concise summaries while embedding a `restorable_reference`. The progressive loader now includes a **skills catalog** that stages SKILL.md metadata, loads full skill content on demand, and queues linked references for future turns. Tool outputs are captured automatically, externalized segments can be persisted to disk via `externalize_write_enabled`, and each stage emits structured metrics that feed the Streamlit dashboard and the e2e logs.


## How It Works

### 1. Asynchronous Task Lifecycle

1. A producer (UI, CLI, CI pipeline) pushes a message to `qc:mailbox/orchestrator` with a `chat_id` and optional `reply_to` target
2. The orchestrator loads the latest checkpoint for that `chat_id`, runs its LangGraph (LLM driver → tool loop), and publishes new envelopes
3. If the workload requires additional capacity or specialized capabilities, the orchestrator spawns new agents on-demand
4. Agents execute tools (LangChain, MCP, custom) and push results back to the orchestrator mailbox
5. The orchestrator emits human-facing updates to `qc:mailbox/human`
6. When tasks complete, the orchestrator cleans up temporary agents to conserve resources

Because checkpoints are bound to `chat_id`, both orchestrator and agents recover mid-task after restarts by automatically rehydrating state from the checkpoint store.

### 2. Dynamic Agent Fleet Management

The orchestrator has full autonomy over the agent fleet through the `agent_management` tool:

**When to spawn agents:**
- Complex tasks requiring parallel execution across multiple specialized agents
- Workload exceeds capacity of current agent fleet
- Tasks need specialized capabilities that benefit from dedicated agents
- Long-running operations that should not block other work

**When to delete agents:**
- Specialized agents complete their assigned tasks
- Reducing resource usage during low-activity periods
- Cleaning up failed or stuck agents

**Operations:**
- `spawn_agent`: Launch new agent containers/pods (auto-generates IDs or accepts custom names)
- `delete_agent`: Stop and remove agent containers/pods
- `list_containers`: View all running agents
- `get_container_status`: Check detailed status of specific agents

The orchestrator checks agent registry status before spawning and delegates work using `reply_to` in message payloads.

### 3. Runtime Model

- Each runtime polls its mailbox, processes a batch, emits outgoing envelopes, then deletes consumed entries
- `chat_id` (UI-generated) and `thread_id` (runtime-populated) identify the logical conversation
- LangGraph graphs use a shared checkpointer: `graph.invoke(state, config={"configurable": {"thread_id": chat_id}})`
- Responses echo the input payload and append the serialized LangChain message trace at `payload.messages`
- Delegation is async by construction: if `payload.reply_to` is present, the orchestrator routes to that agent first
- Spawned agents automatically register with the registry and begin polling their mailbox

### 4. Message Schema

```text
timestamp    ISO-8601 UTC
sender       logical source (human, orchestrator, agent id)
recipient    stream recipient
message      human-readable content
payload {
  chat_id      stable conversation id (UI generated)
  thread_id    LangGraph thread id (populated by runtime)
  ticket_id    optional per-message correlation id
  reply_to     optional delegation directive
  messages     LangChain message trace (list[dict])
  ...          tool- or product-specific metadata
}
```

All services treat payload fields as opaque except for threading/routing attributes.

## Quick Start

### Prerequisites

- **Docker** and **Docker Compose** (for Docker deployment)
- **kubectl** and cluster access (for Kubernetes deployment)
- **uv** (Python package manager): [Installation](https://docs.astral.sh/uv/getting-started/installation/)
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 1. Configure Environment

Copy the samples and add your API keys (at minimum set `ANTHROPIC_API_KEY` for live LLM calls and e2e tests):

```bash
cp .env.sample .env
cp .env.docker.sample .env.docker
# Edit both files and add your keys
```

Notes
- `.env` is used by local tooling and host processes (UI, tests).
- `.env.docker` is mounted into containers by Compose so services inherit the same keys.

### 2. Build Workspace Image

Build the workspace Docker image that agents use for sandboxed execution:

```bash
docker build -f Dockerfile.workspace -t quadracode-workspace:latest .
```

This image provides a Python 3.12 environment with build tools, Node.js, UV, and common development utilities.

### 3. Launch Services (Docker)

```bash
# Bring up all services including UI
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime ui

# Verify services are healthy
docker compose ps
```

The orchestrator will have access to the Docker socket and can spawn additional agents and workspaces dynamically as workload requires.

**Alternatively, run UI locally for development:**

```bash
# Create virtual environment (one-time setup)
uv venv --python 3.12
source .venv/bin/activate  # Optional: or use 'uv run' prefix

# Install dependencies
uv sync

# Launch Streamlit UI
REDIS_HOST=localhost REDIS_PORT=6379 AGENT_REGISTRY_URL=http://localhost:8090 \
  uv run streamlit run quadracode-ui/src/quadracode_ui/app.py
```

### 4. Access the UI

Open **http://localhost:8501** to access the Streamlit control plane. The UI provides:

- **💬 Chat page**: Send messages to the orchestrator with automatic 10-second background polling for responses
- **📡 Mailbox Monitor**: View all Redis Streams traffic with advanced filtering (time range, regex, sender/recipient)
- **📁 Workspaces**: Create/manage Docker-based workspaces, browse files in hierarchical tree view with syntax highlighting
- **📊 Dashboard**: Interactive Plotly charts showing context metrics, agent status, and autonomous event timeline

**Autonomous Mode:**
In the Chat page sidebar, enable **Autonomous Mode** to activate HumanClone operation. Configure guardrails:
- Max iterations (default: 1000)
- Max runtime hours (default: 48)
- Max agents (default: 4)

The orchestrator will operate autonomously within these constraints. Use the emergency stop button if needed to return control to human supervision.

#### HUMAN_OBSOLETE Autonomy Loop

```
┌──────────────────────────────────────────────────────────────────┐
│ Goal Intake                                                       │
│ - Human seeds task + guardrails via Streamlit sidebar             │
├──────────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────┐        ┌────────────────────────┐ │
│ │ Orchestrator Runtime       │◄──────►│ Context Engine (ACE)   │ │
│ │ - Autonomous prompt        │        │ - Progressive loading  │ │
│ │ - Recipient policy         │        │ - Checkpoints &        │ │
│ │ - Guardrail enforcement    │        │   critiques            │ │
│ │ - Event emission           │        └────────┬───────────────┘ │
│ └─────────────┬──────────────┘                 │                 │
│               │ delegates / spawns             │ emits metrics   │
│               ▼                                ▼                 │
│ ┌────────────────────────────┐        ┌────────────────────────┐ │
│ │ Dynamic Agent Fleet        │        │ Redis Streams          │ │
│ │ - Specialized workers      │        │ - qc:mailbox/*         │ │
│ │ - Agent registry health    │        │ - qc:autonomous:events │ │
│ │ - MCP tool discovery       │        └────────┬───────────────┘ │
│ └─────────────┬──────────────┘                 │                 │
│               │                                 │                 │
│               ▼                                 │                 │
│ ┌────────────────────────────┐        ┌─────────┴──────────────┐ │
│ │ UI / Control Plane         │◄──────►│ Observability & Logs   │ │
│ │ - Chat + autonomous tab    │        │ - Dashboard panels     │ │
│ │ - Guardrail settings       │        │ - Redis/metrics tail   │ │
│ │ - Emergency stop control   │        │ - Research exports     │ │
│ └────────────────────────────┘        └────────────────────────┘ │
│                                                                  │
│ Loop: Evaluate → Critique → Plan → Execute → Checkpoint → Emit   │
│ Escalate only on fatal errors or human-triggered emergency stop. │
└──────────────────────────────────────────────────────────────────┘
```

### Endpoints

After launching the stack, access these services:

- **UI (Streamlit)**: http://localhost:8501
- **Agent Registry API**: http://localhost:8090
- **Orchestrator LangGraph Studio** (dev mode): http://localhost:8123
- **Redis CLI**: `docker compose exec -T redis redis-cli ...`
- **Stream tailer**: `bash scripts/tail_streams.sh`
- **Workspace purge**: `bash scripts/agent-management/purge-workspaces.sh --dry-run`

## Kubernetes Deployment

Quadracode supports Kubernetes deployment with the same agent management capabilities:

1. Set `AGENT_RUNTIME_PLATFORM=kubernetes` in orchestrator environment
2. Create Kubernetes secret `quadracode-secrets` with API keys
3. Create PVCs: `quadracode-shared-data`, `quadracode-mcp-cache`
4. Deploy Redis, registry, and orchestrator services
5. The orchestrator will spawn agent pods dynamically via `kubectl`

See `scripts/agent-management/*.sh` for platform-specific implementation details.

## Use Cases

### Code Review Automation
Delegate pull requests to specialized agents: one for style/lint checks, one for security analysis, one for test coverage. The orchestrator spawns reviewers on-demand and terminates them after completion—all coordinated automatically, all resumable after restarts.

### Data Pipeline Orchestration
Trigger long-running ETL jobs, data quality checks, and report generation. The orchestrator tracks progress, spawns pipeline workers as needed, handles retries, and notifies humans when intervention is required.

### Research Synthesis
Ingest documents, dispatch research tasks to multiple agents (summarization, fact-checking, citation extraction). The orchestrator creates a specialized research fleet, aggregates results, produces a final report, then cleans up agents—all while maintaining full audit trails.

### Multi-Service Deployments
Coordinate deployments across microservices: run tests, deploy to staging, run smoke tests, promote to production. The orchestrator spawns deployment agents for each service, handles rollbacks, and maintains checkpoints at every stage so failures don't lose progress.

### Burst Workload Handling
When a spike in requests arrives, the orchestrator automatically scales the agent fleet to handle the load, then scales down during quiet periods to conserve resources.

## Streamlit Control Plane

The Quadracode UI is a comprehensive Streamlit-based control plane providing real-time observability, workspace management, and chat interface.

### Features

**💬 Chat Interface**
- Real-time conversation with orchestrator via background polling (auto-refresh every 10 seconds)
- Human/HumanClone mode toggle for autonomous operation
- Persistent chat history loaded from Redis on startup
- Enhanced message bubbles with color-coding (blue=human, purple=orchestrator, green=agents)
- Markdown rendering and expandable trace/payload views
- "Clear All Context" button to wipe entire deployment state

**📡 Mailbox Monitor**
- Real-time view of all Redis Streams traffic across the system
- Advanced filtering: Time range (5/15/60 min, 24h, custom), Regex search, Sender/Recipient filters
- Message detail panel with full metadata, relative timestamps, copy buttons
- Configurable auto-refresh (1-60 seconds)
- Stream health indicators (✓ Active | ○ Idle | ✗ Error)
- Table/Cards/Detailed display modes

**📁 Workspace Browser**
- Create and destroy Docker-based workspaces with mounted volumes
- Hierarchical file tree with expandable folders (3+ levels deep)
- File metadata display: size (B/KB/MB), modified time, file type
- Syntax-highlighted code viewer (Pygments with monokai theme)
- File search and type filtering
- Export workspace files to local machine
- Workspace event stream viewer
- "Destroy All Workspaces" batch deletion with confirmation

**📊 System Dashboard**
- Interactive Plotly charts: Quality Score Trend, Context Window Usage, Operation Distribution
- Agent activity drill-down with detailed metrics
- Color-coded event timeline for autonomous operations
- Agent status/type distribution (donut and bar charts)
- Configurable history depth and auto-refresh

**⚙️ Prompt Settings**
- System prompt template management
- Variable substitution and preview

The UI uses a background polling thread with blocking `XREAD` on Redis Streams. When new messages arrive, the thread signals the UI to rerun, displaying updates automatically without user interaction. All state (chat history, workspace descriptors, autonomous settings) persists in Redis and survives UI restarts.

## Development

### Local Development Setup

```bash
# Create virtual environment at repo root
uv venv --python 3.12
source .venv/bin/activate  # Optional

# Install all workspace packages in editable mode
uv sync
```

### Run LangGraph Dev Servers

```bash
# Terminal 1: Orchestrator
uv run langgraph dev orchestrator --config quadracode-orchestrator/langgraph-local.json

# Terminal 2: Agent
uv run langgraph dev agent --config quadracode-agent/langgraph-local.json
```

### Testing

> **IMPORTANT FOR AI AGENTS:** All tests require a live Docker stack with real services and API keys. These are integration tests with actual LLM calls, not unit tests.

#### Complete Test Execution Steps for AI Agents

```bash
# STEP 1: Verify prerequisites
# Ensure Docker is running
docker version > /dev/null 2>&1 || echo "ERROR: Docker not running"

# STEP 2: Configure API keys  
# Copy sample files if they don't exist
[ ! -f .env ] && cp .env.sample .env
[ ! -f .env.docker ] && cp .env.docker.sample .env.docker

# Add your ANTHROPIC_API_KEY to both .env files
# The .env file in the root directory is automatically used by all commands
# NO export commands needed - the test runners handle this for you
grep -q "ANTHROPIC_API_KEY=sk-" .env || echo "ERROR: Set ANTHROPIC_API_KEY in .env"
grep -q "ANTHROPIC_API_KEY=sk-" .env.docker || echo "ERROR: Set ANTHROPIC_API_KEY in .env.docker"

# STEP 3: Start the complete Docker stack
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime test-runner

# STEP 4: Wait for services to be healthy (critical!)
sleep 10  # Initial wait for services to start
docker compose ps --services --filter "status=running" | wc -l  # Should show 6

# STEP 5: Run tests INDIVIDUALLY for debugging (5-20 minutes each)
# List available test modules
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh --list"

# Run specific test module
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh foundation_smoke"
docker compose exec test-runner bash -c "cd /app/tests/e2e_advanced && ./run_individual_tests.sh prp_autonomous"
```

Quadracode has two levels of end-to-end testing:

#### Smoke Tests (<5 minutes)

Quick infrastructure validation tests (Docker stack must be running and healthy):

**From host machine:**
```bash
# Infrastructure smoke tests (no LLM calls, validates utilities and setup)
uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v

# UI integration tests (uses stubbed Redis client)
uv run pytest quadracode-ui/tests -q
```

**Inside Docker network (recommended for CI/CD):**
```bash
# Start test runner container
docker compose up -d test-runner

# Run smoke tests inside container
docker compose exec test-runner uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v
```

#### Advanced E2E Tests (60-90 minutes)

Comprehensive, long-running tests that validate the complete system including false-stop detection, PRP cycles, context engineering, autonomous mode, fleet management, workspace integrity, and observability:

**From host machine:**
```bash
# Run all advanced E2E tests
uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO

# Run specific test module
uv run pytest tests/e2e_advanced/test_prp_autonomous.py -v

# Run with increased timeouts for CI or slow environments
E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0 uv run pytest tests/e2e_advanced -v
```

**Inside Docker network (recommended for CI/CD):**
```bash
# Run all tests in container
docker compose exec test-runner uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO

# Run specific module in container
docker compose exec test-runner uv run pytest tests/e2e_advanced/test_prp_autonomous.py -v
```

**Running Tests Individually for Debugging:**
```bash
cd tests/e2e_advanced

# List all test modules with descriptions
./run_individual_tests.sh --list

# Run specific module with output capture
./run_individual_tests.sh foundation_smoke

# Run with verbose debugging output
./run_individual_tests.sh -v prp_autonomous

# Run all modules, stopping at first failure
./run_individual_tests.sh all --stop-on-fail
```

**Prerequisites for Advanced Tests:**
- `ANTHROPIC_API_KEY` must be set in `.env` and `.env.docker` files (no export needed)
- Docker stack must be running: `docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime` and verify health with `docker compose ps --services --filter "status=running"`
- For PRP tests: `docker compose up -d human-clone-runtime` and set `SUPERVISOR_RECIPIENT=human_clone`
- For observability tests: Set `QUADRACODE_TIME_TRAVEL_ENABLED=true`

**Test Modules:**
- `test_foundation_long_run.py` - Sustained message flows (5-10 min)
- `test_context_engine_stress.py` - Context engineering under load (10-15 min)
- `test_prp_autonomous.py` - HumanClone rejection cycles and autonomous execution (15-20 min)
- `test_fleet_management.py` - Dynamic agent lifecycle (5-10 min)
- `test_workspace_integrity.py` - Multi-workspace isolation (10-15 min)
- `test_observability.py` - Time-travel logs and metrics (10-15 min)

After running tests, generate metrics reports:

```bash
# Aggregate metrics from test runs
uv run python tests/e2e_advanced/scripts/aggregate_metrics.py \
  --input "tests/e2e_advanced/metrics/*.json" \
  --output tests/e2e_advanced/reports/aggregate_report.json

# Generate markdown summary
uv run python tests/e2e_advanced/scripts/generate_metrics_report.py \
  --aggregate tests/e2e_advanced/reports/aggregate_report.json \
  --output tests/e2e_advanced/reports/summary_report.md

# Create visualizations
uv run python tests/e2e_advanced/scripts/plot_metrics.py \
  --aggregate tests/e2e_advanced/reports/aggregate_report.json \
  --output tests/e2e_advanced/plots/
```

> See `TESTS.md` and `tests/e2e_advanced/README.md` for complete testing documentation. All tests are full-stack and require valid API keys.

### End‑to‑End Requirements

The e2e suite exercises the full stack (Redis, MCP proxy, registry, orchestrator, agent) against live model/tool backends. To run it reliably:

- Docker + Compose
  - Docker Engine with the v2 compose plugin (runs `docker compose`).
  - Access to `/var/run/docker.sock` (mounted into the orchestrator runtime by compose) so it can spawn and manage agents.
- Internet egress
  - Outbound HTTPS is required for the LLM driver and any MCP‑backed tools used in the flows.
- API keys (set in `.env` and `.env.docker` files - NO export commands needed)
  - `ANTHROPIC_API_KEY` (default driver/reducer/governor use Claude).
  - `OPENAI_API_KEY` (optional; only if you switch models or enable prompt caching experiments).
  - `PERPLEXITY_API_KEY` (optional; used by the Perplexity Ask MCP server if enabled).
  - Other tool keys are optional and only needed if you enable flows that call them: `FIRECRAWL_API_KEY`, `BRIGHT_DATA_API_KEY`, `SCRAPINGBEE_API_KEY`, `GOOGLE_API_KEY`, `BING_SEARCH_API_KEY`, `GITHUB_TOKEN`.
- Ports (defaults, overridable)
  - Redis `6379`, MCP proxy `8000`, Agent Registry `8090`, Orchestrator dev `8123`, Agent dev `8124`, Streamlit UI `8501` (UI container is optional).

Run the suite after bringing up the stack:

```bash
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime

# Quick smoke tests
uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v

# Full comprehensive suite
uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO
```

Environment for compose is sourced from `.env` and `.env.docker` by default. Ensure those files contain valid keys for your environment.

## Repository Layout

```
quadracode/
├── quadracode-runtime/        # Shared runtime core, LangGraph builder, Redis messaging
├── quadracode-orchestrator/   # Orchestrator profile, prompts, CLI entry
├── quadracode-agent/          # Agent profile and prompts
├── quadracode-agent-registry/ # FastAPI service for agent registration and health
├── quadracode-contracts/      # Pydantic envelope contracts and mailbox helpers
├── quadracode-tools/          # Reusable MCP / LangChain tool wrappers (includes workspace management)
├── quadracode-ui/             # Streamlit UI (5 pages: Chat, Mailbox Monitor, Workspaces, Dashboard, Prompt Settings)
├── Dockerfile.workspace       # Workspace container image for sandboxed agent operations
├── scripts/
│   ├── agent-management/      # Shell scripts for spawning/deleting agents (Docker + K8s)
│   └── ...                    # Other operational scripts
├── tests/e2e_advanced/        # Comprehensive E2E test suite with smoke tests
└── docker-compose.yml         # Production deployment configuration
```

## Configuration

Environment variables control runtime behavior:

### Core Configuration
- `QUADRACODE_ID` — runtime identity (overrides profile default)
- `AGENT_REGISTRY_URL` — FastAPI registry endpoint
- `REDIS_HOST`, `REDIS_PORT` — Redis connection
- `SHARED_PATH` — shared volume for MCP transport

### Diagnostics & Logging
- `QUADRACODE_LOG_LEVEL` — root log level for all runtimes (default `INFO`)
- `QUADRACODE_MESSAGING_START_TIMEOUT` — seconds to wait for MCP/Redis messaging to initialize before failing (default `60`)
- `QUADRACODE_AGENT_REGISTRATION_TIMEOUT` — seconds to wait for the agent to register with the registry before aborting startup (default `60`)

### Agent Management
- `AGENT_RUNTIME_PLATFORM` — deployment platform: `docker` (default) or `kubernetes`
- `QUADRACODE_SCRIPTS_DIR` — path to agent management scripts (auto-detected if not set)
- `QUADRACODE_NAMESPACE` — Kubernetes namespace for spawned pods (default: `default`)

### Files
- `.env` — local development environment variables
- `.env.docker` — Docker Compose service overrides (Redis hostnames, MCP endpoints, API keys)

## Observability & Operations

- **Redis stream stats**: `redis-cli XINFO STREAM qc:mailbox/<recipient>`
- **Context engine telemetry**: `redis-cli XRANGE qc:context:metrics - +` to inspect quality components, governor plans, MemAct operation counts, load events, and externalization actions (events: `pre_process`, `curation`, `load`, `externalize`, `tool_response`, `post_process`, `governor_plan`)
- **Externalized segments**: enable persistence with `ContextEngineConfig.externalize_write_enabled` (or env var `QUADRACODE_EXTERNALIZE_WRITE=1`) to write pointer payloads under `external_memory_path`
- **Service logs**: `docker compose logs <service>`
- **Streamlit stream viewer**: Inspect raw payloads and message traces
- **Autonomous guardrails & control events**: `redis-cli XRANGE qc:autonomous:events - +` to review checkpoints, critiques, guardrail triggers, and emergency stops emitted in HUMAN_OBSOLETE mode
- **Agent fleet status**: Check registry API at `/agents` endpoint or use orchestrator's `agent_management` tool
- **Time-travel debugging**: Every stage/transition is mirrored into append-only JSONL logs under `./time_travel_logs`. Use `python -m quadracode_runtime.time_travel replay --log time_travel_logs/<thread>.jsonl --cycle cycle-3` to inspect a loop, or `... diff --cycle-a cycle-2 --cycle-b cycle-4` for improvement deltas.
- **Checkpoint introspection**: From within a runtime, call:
  ```python
  CHECKPOINTER.get_tuple({"configurable": {"thread_id": chat_id, "checkpoint_ns": ""}})
  ```

## Guarantees

- **Persistence**: LangGraph checkpoints keyed by `chat_id` survive process restarts and resume on next invocation
- **Parallelism**: Orchestrator never blocks on agent work; long-running jobs emit incremental envelopes
- **Elasticity**: Agent fleet scales automatically based on orchestrator decisions; spawned agents auto-register and self-terminate
- **Extensibility**: Shared MCP adapters + local tools ensure orchestrator and agents share capabilities automatically

### Supervisor Identity (Human vs HumanClone)

By default, the orchestrator uses the real human (`human`) as the supervising recipient for updates. To run the same system with a HumanClone standing in for the human—without any special autonomous mode awareness—set:

```
QUADRACODE_SUPERVISOR_RECIPIENT=human_clone
```

and run a HumanClone runtime:

```
docker compose up -d human-clone-runtime
```

In this configuration, message flows are simply `human_clone ↔ orchestrator ↔ agent(s)`. Initial tasks are seeded the same way as before; you can send the opening message from the HumanClone (sender `human_clone`) or target the orchestrator with `reply_to` set to the appropriate agent when needed. No special “autonomous mode” flags are required; tools and routing work identically with a different supervisor identity.

The Streamlit UI exposes this as the **“Enable HUMAN_OBSOLETE mode”** toggle. When enabled, all outgoing chat messages originate from the HumanClone mailbox and responses target `qc:mailbox/human_clone`, so the HumanClone stays in the loop continuously.
- **Transparency**: `payload.messages` carries full reasoning traces; UI exposes both human responses and underlying traffic, and `qc:context:metrics` logs every context-engine decision for auditability
- **Platform Portability**: Same codebase runs on Docker Compose and Kubernetes with environment variable configuration

## Contributing

Quadracode is designed to operate as the control plane for "always-on" AI automation—accepting requests continuously, delegating them across a fleet of agents, scaling capacity dynamically, and resuming mid-task without manual babysitting.

We welcome contributions! Whether you're building new agents, adding tool integrations, improving the core runtime, or extending platform support, please open an issue or submit a pull request.

## AI Coding Agents: Development & Testing Guide

### Development Setup

Automated coding assistants working on this codebase should:

1. **Use uv for all Python operations**: `uv venv --python 3.12`, `uv sync`, `uv run <command>`
2. **Install from repo root**: `uv sync` installs all workspace packages in editable mode
3. **Know the architecture**:
   - LangGraph flows and messaging: `quadracode-runtime/src`
   - Orchestrator/agent prompts: `quadracode-orchestrator/`, `quadracode-agent/`
   - Agent management scripts: `scripts/agent-management/` (platform-agnostic shell scripts)
   - Streamlit UI: `quadracode-ui/src/quadracode_ui/app.py`
   - Registry API: `quadracode-agent-registry/src` (FastAPI on port 8090)
   - Contracts: `quadracode-contracts/src`
   - Tools: `quadracode-tools/src` (includes agent_management tool)
   - Tests: `tests/e2e_advanced/` (comprehensive E2E suite with real LLM calls)

### Running E2E Tests - AI Agent Instructions

**IMPORTANT**: These tests make real API calls to LLMs. Ensure your `.env` file contains valid API keys before running.

#### Automated Containerized Test Runner

We provide a containerized test runner that executes tests INSIDE a Docker container with clear, verbose output optimized for AI monitoring. The container automatically uses the `.env` file from your repository root - no manual export commands needed.

**How it works:**
1. The script builds and runs a Docker container
2. Tests execute INSIDE the container (isolated environment)
3. Output streams to your terminal in real-time
4. Container logs are available via `docker logs` for monitoring
5. Your `.env` file is automatically passed to the container

##### Prerequisites

1. **Docker must be running**:
   ```bash
   docker version  # Should show both client and server info
   ```

2. **API keys configured in `.env`**:
   ```bash
   # Verify .env exists and has your API key
   # NO export command needed - the test runner handles this automatically
   grep ANTHROPIC_API_KEY .env  # Should show: ANTHROPIC_API_KEY=sk-ant-...
   ```

3. **Services are running**:
   ```bash
   # Start required services
   docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime
   
   # Verify (should show 5 services)
   docker compose ps --services --filter "status=running" | wc -l
   ```

##### Running Tests

**Simplest Method - Use the convenience script**:
```bash
# Automatically uses .env file from repo root - no export needed!
./run_tests_containerized.sh smoke       # Quick validation, no LLM calls
./run_tests_containerized.sh integration  # Include Docker operations  
./run_tests_containerized.sh full        # Complete suite with LLM calls

# Monitor test progress (in another terminal):
docker logs -f quadracode-test-runner  # Tail container logs
```

**Manual Docker commands** (if you prefer):
```bash
# Build the test runner container (first time only)
docker build -f tests/Dockerfile.test-runner -t quadracode-test-runner .

# Run SMOKE tests (no LLM calls, ~30 seconds)
docker run --rm --network="host" \
  --env-file .env \
  -e TEST_MODE="smoke" \
  quadracode-test-runner

# Run INTEGRATION tests (includes Docker operations, ~5 minutes)
docker run --rm --network="host" \
  --env-file .env \
  -e TEST_MODE="integration" \
  quadracode-test-runner

# Run FULL test suite (real LLM calls, 60-90 minutes)
docker run --rm --network="host" \
  --env-file .env \
  -e TEST_MODE="full" \
  quadracode-test-runner
```

##### What the Test Runner Does

The containerized test runner (`tests/run_tests_in_container.py`) provides:
- ✅ Clear pass/fail indicators with emojis
- 🔄 Real-time progress updates
- ⏱️ Timeout protection (tests that hang are killed)
- 📊 Summary statistics at the end
- 🚨 Loud error reporting for failures

**For AI Agents Monitoring Tests:**
- Tests run INSIDE the container (isolated environment)
- Output streams to both terminal AND container logs
- Monitor progress with: `docker logs -f quadracode-test-runner`
- Check container status: `docker ps | grep test-runner`
- Container automatically inherits `.env` file - no manual configuration needed

##### Monitoring Test Output

The test runner produces output like this:
```
[07:50:59] ℹ️ Container Environment:
  API Key: ✓ Set (length: 108)
  Test Mode: SMOKE
  
[07:50:59] 🔄 Running: test_foundation_smoke.py
  ✓ test_imports_and_structure PASSED
  ✓ test_logging_infrastructure PASSED
  
[07:51:03] ✅ ALL TESTS PASSED! (6/6)
```

#### Alternative: Run Tests Locally (Not Recommended for AI)

If you need to run tests outside the container:

```bash
# Install dependencies
uv sync

# Run smoke tests
uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v

# Run with timeout protection (recommended)
timeout 300 uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v
```

### Test Types and Expected Durations

| Test Mode | Duration | LLM Calls | Purpose |
|-----------|----------|-----------|----------|
| smoke | ~30 seconds | No | Quick infrastructure validation |
| integration | ~5 minutes | No | Container and service integration |
| full | 60-90 minutes | Yes | Complete E2E validation with real AI |

### Troubleshooting for AI Agents

- **Container can't find Docker**: This is normal - the test container doesn't need Docker access
- **API key not found**: Ensure `.env` exists in repo root with `ANTHROPIC_API_KEY=sk-ant-...`
- **Tests hanging**: The container has built-in timeouts - if a test hangs >30s it will be killed
- **Services not healthy**: Run `docker compose restart` to restart all services

This workflow ensures repeatable builds and full E2E test coverage with real LLM interactions.

## Documentation

- **[TESTING.md](TESTING.md)** - Complete guide for running E2E tests with live LLM API calls, monitoring system behavior, and debugging
- **[IMPLEMENTATION.md](IMPLEMENTATION.md)** - Detailed implementation notes for each component
- **[TECHNICAL_IMPLEMENTATION.md](TECHNICAL_IMPLEMENTATION.md)** - Technical architecture and design decisions

## License

[Add your license here]

## Learn More

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Redis Streams](https://redis.io/docs/data-types/streams/)

---

**Quadracode**: Build self-scaling AI agent systems that work when you sleep.
### Context Engine Configuration

You can tune the context engine at runtime using environment variables (all optional). These are picked up automatically by the orchestrator and agent runtimes.

- `QUADRACODE_CONTEXT_WINDOW_MAX` (int, tokens)
- `QUADRACODE_TARGET_CONTEXT_SIZE` (int, tokens) — default 10,000
- `QUADRACODE_MAX_TOOL_PAYLOAD_CHARS` (int, characters)
- `QUADRACODE_REDUCER_MODEL` (string, e.g., `heuristic` to avoid live LLM)
- `QUADRACODE_REDUCER_CHUNK_TOKENS` (int, tokens)
- `QUADRACODE_REDUCER_TARGET_TOKENS` (int, tokens)
- `QUADRACODE_GOVERNOR_MODEL` (string, e.g., `heuristic`)
- `QUADRACODE_GOVERNOR_MAX_SEGMENTS` (int)
- `QUADRACODE_METRICS_ENABLED` (bool: `1|true|yes`)
- `QUADRACODE_METRICS_EMIT_MODE` (`stream|log`)
- `QUADRACODE_METRICS_REDIS_URL` (string)
- `QUADRACODE_METRICS_STREAM_KEY` (string)
- `QUADRACODE_EXTERNALIZE_WRITE_ENABLED` (bool)
- `QUADRACODE_QUALITY_THRESHOLD` (float 0..1)

Units
- “Context window” and “target size” are measured in approximate tokens (whitespace-delimited). The context engine tracks per-segment `token_count` and sums to compute `context_window_used`.
- `MAX_TOOL_PAYLOAD_CHARS` is characters of tool output before reduction is applied.

Example (force compression/externalization during tests):

```bash
export QUADRACODE_MAX_TOOL_PAYLOAD_CHARS=10
export QUADRACODE_TARGET_CONTEXT_SIZE=10
export QUADRACODE_REDUCER_MODEL=heuristic
```

## Local Development & Testing

- Sync dependencies: `uv sync`
- Run package-specific tests: `uv run pytest` (from within a package directory)
- Run smoke tests: `uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v`
- Run comprehensive E2E tests: `uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO`
- Launch local LangGraph dev server: `uv run langgraph dev agent --config quadracode-agent/langgraph-local.json` (swap `agent` with `orchestrator` as needed)

## Docker Stack

```bash
# Build workspace image (one-time)
docker build -f Dockerfile.workspace -t quadracode-workspace:latest .

# Bring up all services including UI
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime ui

# Verify services are running
docker compose ps

# Access UI at http://localhost:8501
# Tail streams: bash scripts/tail_streams.sh
```

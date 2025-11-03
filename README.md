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
- **MCP Integration**: Standardized tool interfaces via Model Context Protocol for seamless agent capability sharing
- **Full Observability**: Streamlit control plane with conversation management, real-time stream inspection, and message tracing
- **Platform Agnostic**: Runs on Docker Compose or Kubernetes with the same codebase
- **Production Ready**: Comprehensive E2E tests, structured message contracts, fault-tolerant design

## Architecture Overview

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  Streamlit  │◄────►│ Redis Streams    │◄────►│  Orchestrator   │
│     UI      │      │  (Event Fabric)  │      │    Runtime      │
└─────────────┘      └──────────────────┘      └────────┬────────┘
                              ▲                         │
                              │                         │ spawns/deletes
                              │                         ▼
                     ┌────────┴────────┐      ┌─────────────────┐
                     │  Agent Registry │      │  Dynamic Agent  │
                     │   (FastAPI)     │      │      Fleet      │
                     └─────────────────┘      └─────────────────┘
```

### Core Components

- **Redis Streams** (`qc:mailbox/<recipient>`) provide durable, ordered mailboxes for every participant
- **LangGraph runtimes** consume their mailbox, execute stateful graphs, and publish responses back onto the fabric
- **Agent Registry** (FastAPI on port `8090`) tracks agent identities, health, and ports for dynamic routing
- **Agent Management** scripts and tools enable the orchestrator to spawn/delete agents autonomously based on workload
- **Redis-MCP proxy** exposes the Redis transport to MCP-compatible clients for runtime tool loading
- **Streamlit UI** (`8501`) provides multi-chat management, live stream inspection, and trace visualization

Every message is a simple envelope: `timestamp`, `sender`, `recipient`, `message`, and `payload` (JSON). The payload carries threading metadata, correlation IDs, tool outputs, and routing hints.

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

### 1. Launch Services (Docker)

```bash
# Bring up Redis, registry, orchestrator, and agent runtimes
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime

# Verify services are healthy
docker compose ps
```

The orchestrator will have access to the Docker socket and can spawn additional agents dynamically as workload requires.

### 2. Run the UI

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

### 3. Access the UI

Open http://localhost:8501 and start a conversation. The orchestrator will delegate work to agents and dynamically scale the fleet as needed. You'll see real-time updates as the system processes your request.

**Optional**: Containerize the UI by uncommenting the `ui` service in `docker-compose.yml`:

```bash
docker compose up -d ui
```

### Endpoints

- **UI**: http://localhost:8501
- **Agent registry**: http://localhost:8090
- **Redis CLI**: `docker compose exec -T redis redis-cli ...`
- **Stream tailer**: `bash scripts/tail_streams.sh`

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

- **Chat sidebar**: ChatGPT-style conversation list (new chat, rename, switch). Each chat persists its history and baseline stream offset
- **Chat view**: Real-time conversation with trace expanders revealing `payload.messages` for every response
- **Streams tab**: Raw Redis mailbox inspector with manual refresh, ordering controls, and JSON payload display
- **Registry panel**: Summaries of total/healthy agents; orchestrator determines routing and scaling dynamically

The UI writes `chat_id` and `ticket_id` into each payload. A per-session watcher thread maintains a blocking `XREAD` on `qc:mailbox/human`; when a new envelope arrives, it triggers a Streamlit rerun. The interface remains responsive even when no traffic is flowing.

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

```bash
# Runtime memory regression (checkpoint persistence)
uv run pytest tests/test_runtime_memory.py

# End-to-end (orchestrates Docker Compose, validates registry/tools/chat)
uv run pytest tests/e2e -m e2e

# UI integration (live Redis, no stubs)
uv run pytest quadracode-ui/tests -m integration -q

# UI E2E (full stack, UI runs locally via AppTest)
# Prerequisite: docker compose up redis agent-registry orchestrator-runtime agent-runtime
uv run pytest quadracode-ui/tests -m e2e -q
```

## Repository Layout

```
quadracode/
├── quadracode-runtime/        # Shared runtime core, LangGraph builder, Redis messaging
├── quadracode-orchestrator/   # Orchestrator profile, prompts, CLI entry
├── quadracode-agent/          # Agent profile and prompts
├── quadracode-agent-registry/ # FastAPI service for agent registration and health
├── quadracode-contracts/      # Pydantic envelope contracts and mailbox helpers
├── quadracode-tools/          # Reusable MCP / LangChain tool wrappers (includes agent_management)
├── quadracode-ui/             # Streamlit chat client with multi-thread controls
├── scripts/
│   ├── agent-management/      # Shell scripts for spawning/deleting agents (Docker + K8s)
│   └── ...                    # Other operational scripts
├── tests/                     # Unit and integration test suites
└── docker-compose.yml         # Production deployment configuration
```

## Configuration

Environment variables control runtime behavior:

### Core Configuration
- `QUADRACODE_ID` — runtime identity (overrides profile default)
- `AGENT_REGISTRY_URL` — FastAPI registry endpoint
- `REDIS_HOST`, `REDIS_PORT` — Redis connection
- `SHARED_PATH` — shared volume for MCP transport

### Agent Management
- `AGENT_RUNTIME_PLATFORM` — deployment platform: `docker` (default) or `kubernetes`
- `QUADRACODE_SCRIPTS_DIR` — path to agent management scripts (auto-detected if not set)
- `QUADRACODE_NAMESPACE` — Kubernetes namespace for spawned pods (default: `default`)

### Files
- `.env` — local development environment variables
- `.env.docker` — Docker Compose service overrides (Redis hostnames, MCP endpoints, API keys)

## Observability & Operations

- **Redis stream stats**: `redis-cli XINFO STREAM qc:mailbox/<recipient>`
- **Service logs**: `docker compose logs <service>`
- **Streamlit stream viewer**: Inspect raw payloads and message traces
- **Agent fleet status**: Check registry API at `/agents` endpoint or use orchestrator's `agent_management` tool
- **Checkpoint introspection**: From within a runtime, call:
  ```python
  CHECKPOINTER.get_tuple({"configurable": {"thread_id": chat_id, "checkpoint_ns": ""}})
  ```

## Guarantees

- **Persistence**: LangGraph checkpoints keyed by `chat_id` survive process restarts and resume on next invocation
- **Parallelism**: Orchestrator never blocks on agent work; long-running jobs emit incremental envelopes
- **Elasticity**: Agent fleet scales automatically based on orchestrator decisions; spawned agents auto-register and self-terminate
- **Extensibility**: Shared MCP adapters + local tools ensure orchestrator and agents share capabilities automatically
- **Transparency**: `payload.messages` carries full reasoning traces; UI exposes both human responses and underlying traffic
- **Platform Portability**: Same codebase runs on Docker Compose and Kubernetes with environment variable configuration

## Contributing

Quadracode is designed to operate as the control plane for "always-on" AI automation—accepting requests continuously, delegating them across a fleet of agents, scaling capacity dynamically, and resuming mid-task without manual babysitting.

We welcome contributions! Whether you're building new agents, adding tool integrations, improving the core runtime, or extending platform support, please open an issue or submit a pull request.

## AI Coding Agents: Development Notes

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
4. **Run tests with uv**: `uv run pytest` (package-specific or from repo root)
5. **Observe Redis traffic**: `scripts/tail_streams.sh` or `redis-cli monitor`
6. **Agent management**: Scripts in `scripts/agent-management/` support both Docker and Kubernetes

This workflow ensures repeatable builds and full repository context for AI-assisted development.

## License

[Add your license here]

## Learn More

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Redis Streams](https://redis.io/docs/data-types/streams/)

---

**Quadracode**: Build self-scaling AI agent systems that work when you sleep.

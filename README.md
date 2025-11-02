# Quadracode

**An always-on, LangGraph-native orchestration platform for asynchronous, long-running AI workloads.**

Quadracode is a production-ready framework that enables AI agents to handle complex, multi-step tasks that span minutes, hours, or days—without blocking, without losing state, and without manual intervention. Built on Redis Streams, LangGraph checkpointing, and MCP-aware tooling, Quadracode provides the infrastructure you need to deploy resilient, distributed AI agent systems.

## Why Quadracode?

Most AI agent frameworks are designed for synchronous, short-lived interactions. Quadracode is purpose-built for **real-world automation** where:

- **Tasks take time**: Code reviews, data analysis, multi-service deployments, research synthesis
- **Failures happen**: Network issues, rate limits, service restarts—your work should survive them
- **Delegation is essential**: One orchestrator coordinates multiple specialized agents, each with their own tools and responsibilities
- **Observability matters**: Every decision, tool call, and message is traced and inspectable

### Key Features

- **Persistent State**: LangGraph checkpoints keyed by conversation ID survive process restarts and resume exactly where they left off
- **Async-First**: Orchestrator never blocks on agent work; long-running jobs emit incremental updates over Redis Streams
- **Multi-Agent Coordination**: Built-in service registry, dynamic routing, and agent health tracking
- **MCP Integration**: Standardized tool interfaces via Model Context Protocol for seamless agent capability sharing
- **Full Observability**: Streamlit control plane with conversation management, real-time stream inspection, and message tracing
- **Production Ready**: Docker Compose deployment, comprehensive E2E tests, structured message contracts

## Architecture Overview

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────┐
│  Streamlit  │◄────►│ Redis Streams    │◄────►│ Orchestrator│
│     UI      │      │  (Event Fabric)  │      │   Runtime   │
└─────────────┘      └──────────────────┘      └─────────────┘
                              ▲                        │
                              │                        ▼
                     ┌────────┴────────┐      ┌─────────────┐
                     │  Agent Registry │      │    Agent    │
                     │   (FastAPI)     │      │   Runtime   │
                     └─────────────────┘      └─────────────┘
```

### Core Components

- **Redis Streams** (`qc:mailbox/<recipient>`) provide durable, ordered mailboxes for every participant
- **LangGraph runtimes** consume their mailbox, execute stateful graphs, and publish responses back onto the fabric
- **Agent Registry** (FastAPI on port `8090`) tracks agent identities, health, and ports for dynamic routing
- **Redis-MCP proxy** exposes the Redis transport to MCP-compatible clients for runtime tool loading
- **Streamlit UI** (`8501`) provides multi-chat management, live stream inspection, and trace visualization

Every message is a simple envelope: `timestamp`, `sender`, `recipient`, `message`, and `payload` (JSON). The payload carries threading metadata, correlation IDs, tool outputs, and routing hints.

## How It Works

### 1. Asynchronous Task Lifecycle

1. A producer (UI, CLI, CI pipeline) pushes a message to `qc:mailbox/orchestrator` with a `chat_id` and optional `reply_to` target
2. The orchestrator loads the latest checkpoint for that `chat_id`, runs its LangGraph (LLM driver → tool loop), and publishes new envelopes
3. Agents execute tools (LangChain, MCP, custom) and push results back to the orchestrator mailbox
4. The orchestrator emits human-facing updates to `qc:mailbox/human`

Because checkpoints are bound to `chat_id`, both orchestrator and agents recover mid-task after restarts by automatically rehydrating state from the checkpoint store.

### 2. Runtime Model

- Each runtime polls its mailbox, processes a batch, emits outgoing envelopes, then deletes consumed entries
- `chat_id` (UI-generated) and `thread_id` (runtime-populated) identify the logical conversation
- LangGraph graphs use a shared checkpointer: `graph.invoke(state, config={"configurable": {"thread_id": chat_id}})`
- Responses echo the input payload and append the serialized LangChain message trace at `payload.messages`
- Delegation is async by construction: if `payload.reply_to` is present, the orchestrator routes to that agent first

### 3. Message Schema

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

- **Docker** and **Docker Compose**
- **uv** (Python package manager): [Installation](https://docs.astral.sh/uv/getting-started/installation/)
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 1. Launch Services

```bash
# Bring up Redis, registry, orchestrator, and agent runtimes
docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime

# Verify services are healthy
docker compose ps
```

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

Open http://localhost:8501 and start a conversation. The orchestrator will delegate work to agents, and you'll see real-time updates as the system processes your request.

**Optional**: Containerize the UI by uncommenting the `ui` service in `docker-compose.yml`:

```bash
docker compose up -d ui
```

### Endpoints

- **UI**: http://localhost:8501
- **Agent registry**: http://localhost:8090
- **Redis CLI**: `docker compose exec -T redis redis-cli ...`
- **Stream tailer**: `bash scripts/tail_streams.sh`

## Use Cases

### Code Review Automation
Delegate pull requests to specialized agents: one for style/lint checks, one for security analysis, one for test coverage—all coordinated by the orchestrator, all resumable after restarts.

### Data Pipeline Orchestration
Trigger long-running ETL jobs, data quality checks, and report generation. The orchestrator tracks progress, handles retries, and notifies humans when intervention is needed.

### Research Synthesis
Ingest documents, dispatch research tasks to multiple agents (summarization, fact-checking, citation extraction), aggregate results, and produce a final report—all while maintaining full audit trails.

### Multi-Service Deployments
Coordinate deployments across microservices: run tests, deploy to staging, run smoke tests, promote to production—with checkpoints at every stage so failures don't lose progress.

## Streamlit Control Plane

- **Chat sidebar**: ChatGPT-style conversation list (new chat, rename, switch). Each chat persists its history and baseline stream offset
- **Chat view**: Real-time conversation with trace expanders revealing `payload.messages` for every response
- **Streams tab**: Raw Redis mailbox inspector with manual refresh, ordering controls, and JSON payload display
- **Registry panel**: Summaries of total/healthy agents; orchestrator determines routing dynamically

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
├── quadracode-tools/          # Reusable MCP / LangChain tool wrappers
├── quadracode-ui/             # Streamlit chat client with multi-thread controls
├── scripts/                   # Operational scripts (stream tailer, agent launcher)
├── tests/                     # Unit and integration test suites
└── docker-compose.yml         # Production deployment configuration
```

## Configuration

Environment variables control runtime behavior:

- `QUADRACODE_ID` — runtime identity (overrides profile default)
- `AGENT_REGISTRY_URL` — FastAPI registry endpoint
- `REDIS_HOST`, `REDIS_PORT` — Redis connection
- `SHARED_PATH` — shared volume for MCP transport
- `.env`, `.env.docker` — shared environment definitions for Compose services

## Observability & Operations

- **Redis stream stats**: `redis-cli XINFO STREAM qc:mailbox/<recipient>`
- **Service logs**: `docker compose logs <service>`
- **Streamlit stream viewer**: Inspect raw payloads and message traces
- **Checkpoint introspection**: From within a runtime, call:
  ```python
  CHECKPOINTER.get_tuple({"configurable": {"thread_id": chat_id, "checkpoint_ns": ""}})
  ```

## Guarantees

- **Persistence**: LangGraph checkpoints keyed by `chat_id` survive process restarts and resume on next invocation
- **Parallelism**: Orchestrator never blocks on agent work; long-running jobs emit incremental envelopes
- **Extensibility**: Shared MCP adapters + local tools ensure orchestrator and agents share capabilities automatically
- **Transparency**: `payload.messages` carries full reasoning traces; UI exposes both human responses and underlying traffic

## Contributing

Quadracode is designed to operate as the control plane for "always-on" AI automation—accepting requests continuously, delegating them across a fleet of agents, and resuming mid-task without manual babysitting.

We welcome contributions! Whether you're building new agents, adding tool integrations, or improving the core runtime, please open an issue or submit a pull request.

## AI Coding Agents: Development Notes

Automated coding assistants working on this codebase should:

1. **Use uv for all Python operations**: `uv venv --python 3.12`, `uv sync`, `uv run <command>`
2. **Install from repo root**: `uv sync` installs all workspace packages in editable mode
3. **Know the architecture**:
   - LangGraph flows and messaging: `quadracode-runtime/src`
   - Orchestrator/agent prompts: `quadracode-orchestrator/`, `quadracode-agent/`
   - Streamlit UI: `quadracode-ui/src/quadracode_ui/app.py`
   - Registry API: `quadracode-agent-registry/src` (FastAPI on port 8090)
   - Contracts: `quadracode-contracts/src`
4. **Run tests with uv**: `uv run pytest` (package-specific or from repo root)
5. **Observe Redis traffic**: `scripts/tail_streams.sh` or `redis-cli monitor`

This workflow ensures repeatable builds and full repository context for AI-assisted development.

## License

[Add your license here]

## Learn More

- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Redis Streams](https://redis.io/docs/data-types/streams/)

---

**Quadracode**: Build AI agent systems that work when you sleep.

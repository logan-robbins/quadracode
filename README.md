# Quadracode

**An always-on, LangGraph-native orchestration platform for asynchronous, long-running AI workloads.**

Quadracode is a production-ready framework for deploying resilient AI agent systems that span minutes, hours, or days. It features dynamic fleet management, persistent workspace environments, and full observability.

<img width="1594" height="911" alt="image" src="https://github.com/user-attachments/assets/5298067f-acd6-41b7-aff5-1eb3bab93ccb" />


## 🚀 Quick Start

### 1. Prerequisites
- **Docker** & **Docker Compose**
- **Python 3.12** + [uv](https://github.com/astral-sh/uv)
- `ANTHROPIC_API_KEY` (for best results)

### 2. Configure
```bash
cp .env.sample .env
cp .env.docker.sample .env.docker
# Edit .env and .env.docker to add your ANTHROPIC_API_KEY
```

### 3. Build & Launch
Initialize the workspace base image and start the stack:

```bash
# Build the sandboxed workspace image
docker build -f Dockerfile.workspace -t quadracode-workspace:latest .

# Launch all services
docker compose up -d
```

### 4. Verify
Access the **Streamlit Control Plane** at **http://localhost:8501**.

You should see the **Default Workspace** automatically listed in the Workspaces tab.

---

## 🗄 Checkpoint Persistence (PostgreSQL)

LangGraph conversation state is persisted to PostgreSQL via `AsyncPostgresSaver` (`langgraph-checkpoint-postgres>=3.0`). This ensures conversation threads, tool calls, and autonomous state survive container restarts.

**How it works:**
- `DATABASE_URL` env var controls the backend: when set → `AsyncPostgresSaver` with `psycopg` async driver and `psycopg_pool.AsyncConnectionPool`; when unset → `MemorySaver` (in-memory, lost on restart).
- `docker-compose.yml` sets `DATABASE_URL=postgresql://quadracode:quadracode@postgres:5432/quadracode` in the shared `x-python-env` anchor. All runtime services (orchestrator, agent, human-clone) inherit it.
- The `postgres` service uses `postgres:16-alpine` with a `postgres-data` named volume for durability.
- Checkpoint tables are created automatically via `checkpointer.setup()` on first startup.
- Pool sizing: `QUADRACODE_PG_POOL_MIN_SIZE` (default 2), `QUADRACODE_PG_POOL_MAX_SIZE` (default 20), `QUADRACODE_PG_OPEN_TIMEOUT` (default 30s).

**Local dev without Postgres:** leave `DATABASE_URL` unset or use `QUADRACODE_MOCK_MODE=true`. The runtime falls back to `MemorySaver`.

---

## 🏗 System Components

### Orchestrator (`quadracode-orchestrator`)
The brain of the system. It consumes tasks from `qc:mailbox/orchestrator`, maintains conversation state via LangGraph checkpoints, and dynamically spawns agent containers to handle burst workloads or specialized tasks. It never blocks; it polls for results and emits incremental updates.

**Modes**: Configured via `QUADRACODE_MODE` / `QUADRACODE_AUTONOMOUS_MODE` / `HUMAN_OBSOLETE_MODE` env vars.
- **Standard**: Human-supervised. System prompt in `prompts/system.py`. Orchestrator responds to human via UI.
- **Autonomous (HUMAN_OBSOLETE)**: Fully autonomous. System prompt in `prompts/autonomous.py`. Orchestrator runs a decision loop (evaluate → critique → plan → execute → checkpoint) with fleet management. Supervisor feedback is routed through the PRP trigger system — the orchestrator sees "Supervisor Review Feedback" system messages, never raw supervisor JSON. All internal state keys use `supervisor_*` naming (`supervisor_trigger`, `supervisor_requirements`, `supervisor_triggered`).

**Supervisor** (`prompts/human_clone.py`): Quality gate persona. Outputs structured `HumanCloneTrigger` JSON consumed by `prp_trigger_check` node. The orchestrator never sees the raw output — the runtime intercepts it and injects a sanitized summary. Supervisor identity is `QUADRACODE_SUPERVISOR_RECIPIENT` env var (`human`, `supervisor`, or `human_clone` for backward compat). Internal state keys use `supervisor_*` naming (e.g., `supervisor_trigger`, `supervisor_requirements`, `supervisor_triggered`). Profile name: `supervisor` (accepts `human_clone` for backward compat).

**Prompt structure**: All prompts use XML tags for Claude optimization. Profile selection in `profile.py` logs the active mode at startup.

<img width="1587" height="908" alt="image" src="https://github.com/user-attachments/assets/c65110b7-da6f-4c5c-aab0-94c3f328bde7" />

### Agents (`quadracode-agent`)
Specialized workers spawned on-demand. They are **purely remote drivers**.
*   **No Local Execution**: Agents do NOT run code in their own containers.
*   **Remote Control**: They use tools to drive the `quadracode-workspace` container.
*   **Ephemeral**: Agents can be destroyed at any time without losing project state (which lives in the workspace).

<img width="1585" height="914" alt="image" src="https://github.com/user-attachments/assets/5f306353-bdda-40d1-987a-bccdb5fbeff1" />

### Workspace Engine (`quadracode-workspace`)
A strict, sandboxed Docker environment where all actual work happens.
*   **/workspace**: The execution root. Ephemeral builds, tests, and code execution happen here.
*   **/shared**: High-speed volume mounted RW to all agents. used for passing large artifacts between agents.
*   **Default Workspace**: A `workspace-default` service is always available for general tasks.

<img width="1593" height="909" alt="image" src="https://github.com/user-attachments/assets/77d2a3fc-47be-4662-996f-2148812e30e3" />


### Control Plane (`quadracode-ui`)
Streamlit ≥1.40 application for full observability.  Python ≥3.12, Pydantic ≥2.0, Redis ≥5.0, Plotly ≥5.18.  Uses `@st.fragment(run_every=N)` for non-blocking auto-refresh (no `time.sleep` blocking).  All typing uses Python 3.12+ built-in generics (`dict`, `list`, `X | None`).  `@st.cache_resource` for Redis connection pooling, `@st.cache_data(ttl=…)` for API caching.  Mock mode via `QUADRACODE_MOCK_MODE=true` (fakeredis, no external deps).  Pages: Chat (background `PollingThread` + fragment auto-refresh), Mailbox Monitor (regex filters, table/card/detailed views, fragment auto-refresh), Workspaces (create/destroy, file browser with Pygments highlighting, snapshots, export), Dashboard (agent registry, context metrics, autonomous events, Plotly charts), Prompt Settings (governor/reducer/compression config sync via Redis pub/sub).  Run: `cd quadracode-ui && uv sync && QUADRACODE_MOCK_MODE=true uv run streamlit run src/quadracode_ui/app.py`.
*   **Chat**: Real-time interaction with `@st.fragment(run_every=10)` background polling.
*   **Mailbox Monitor**: Regex-filtered view of the Redis Stream event fabric with `@st.fragment` auto-refresh.
*   **Workspace Browser**: File explorer for the Docker volumes with snapshots and diff comparison.
*   **Dashboard**: Agent registry, context metrics, autonomous events with Plotly charts.


### Agent Registry (`quadracode-agent-registry`)
FastAPI service (Port 8090) for agent discovery and health monitoring.  Python ≥3.12, FastAPI ≥0.115, Pydantic ≥2.10, pydantic-settings ≥2.6.  Uses modern FastAPI lifespan context manager (not deprecated `@app.on_event`), Pydantic v2 `ConfigDict`/`SettingsConfigDict`, Python 3.12+ built-in types (`list`, `dict`, `X | None`), timezone-aware `datetime.now(timezone.utc)`.  SQLite-backed with thread-safe connection management, parameterised queries, idempotent schema migrations.  Endpoints: `POST /agents/register` (201), `POST /agents/{id}/heartbeat`, `GET /agents`, `GET /agents/hotpath`, `GET /agents/{id}`, `DELETE /agents/{id}`, `POST /agents/{id}/hotpath`, `GET /stats`, `GET /health`.  All endpoints have typed `response_model` declarations.  Hotpath agents are protected from removal unless `force=true`.  Tests: `cd quadracode-agent-registry && uv run pytest tests/ -v --confcutdir=.`.

### Contracts (`quadracode-contracts`)
Shared Pydantic v2 data models — the "language" all services speak.  Python ≥3.12, Pydantic ≥2.10.  Modules: `messaging` (MessageEnvelope, mailbox routing, `SUPERVISOR_RECIPIENT` alias), `workspace` (WorkspaceDescriptor, CommandResult), `autonomous` (RoutingDirective, Checkpoint, Escalation), `human_clone` (HumanCloneTrigger, ExhaustionMode — contract models kept for schema compat), `agent_registry` (AgentRegistrationRequest, AgentInfo, AgentHeartbeat, RegistryStats), `agent_id` (generate_agent_id).  All models use Python 3.12+ built-in types (`list`, `dict`, `X | None`).  `SUPERVISOR_RECIPIENT` is the preferred constant; `HUMAN_CLONE_RECIPIENT` kept as backward-compatible alias.

### Tools (`quadracode-tools`)
Shared LangChain tool definitions used by agents and orchestrator.  Python ≥3.12, langchain-core ≥1.0, Pydantic ≥2.0.  Entry point: `from quadracode_tools import get_tools` returns 20 `BaseTool` instances.  Categories: filesystem (`read_file`, `write_file`), shell (`bash_shell`, `python_repl`), workspace management (`workspace_create`, `workspace_exec`, `workspace_copy_to`, `workspace_copy_from`, `workspace_destroy`, `workspace_info`), agent lifecycle (`agent_registry`, `agent_management`), autonomous control (`autonomous_checkpoint`, `autonomous_escalate`, `hypothesis_critique`, `request_final_review`), testing (`run_full_test_suite`, `generate_property_tests`), meta-cognition (`manage_refinement_ledger`, `inspect_context_engine`).  All tools use Pydantic v2 input schemas, `model_dump()`, Python 3.12+ built-in type annotations, and return structured JSON.  MCP client (`quadracode_tools.client`) uses persistent httpx connection pooling.  Tests: `cd quadracode-tools && uv run pytest tests/ -v`.

---

## 🛠 Development Commands

**Local UI Development:**
```bash
cd quadracode-ui && uv sync
QUADRACODE_MOCK_MODE=true uv run streamlit run src/quadracode_ui/app.py
```

**Run Tests:**
```bash
# Infrastructure smoke tests
uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v

# Full E2E suite (requires running stack)
uv run pytest tests/e2e_advanced -m e2e_advanced

# Contracts package unit tests (isolated — no stack required)
cd quadracode-contracts && uv run pytest tests/ -v --confcutdir=.
```

**Tail Logs:**
```bash
docker compose logs -f orchestrator-runtime
```

---

## 🧠 Memory & Context Compression Engine

Quadracode implements a sophisticated context engineering system for long-running AI workloads.

### Architecture

| Component | Location | Purpose |
|-----------|----------|---------|
| **ContextEngine** | `quadracode-runtime/src/quadracode_runtime/nodes/context_engine.py` | Central coordinator for all context operations |
| **ContextReducer** | `quadracode-runtime/src/quadracode_runtime/nodes/context_reducer.py` | LLM-based compression (chunks → summaries → combined) |
| **ContextResetAgent** | `quadracode-runtime/src/quadracode_runtime/nodes/context_reset.py` | Hard reset at 92% saturation, persists to disk |
| **LongTermMemory** | `quadracode-runtime/src/quadracode_runtime/long_term_memory.py` | Episodic (200 max) + Semantic (100 max) memory |
| **Checkpointer** | `quadracode-runtime/src/quadracode_runtime/graph.py` | LangGraph MemorySaver (dev) / AsyncPostgresSaver (prod, via `DATABASE_URL`) |

### Context Flow

```
START → prp_trigger_check → context_pre → context_governor → driver → context_post → [tools] → context_tool → driver → END
```

### Key Configuration (from `config/context_engine.py`)

- `context_window_max`: 128,000 tokens
- `optimal_context_size`: 10,000 tokens
- `message_budget_ratio`: 0.6 (60% for messages)
- `min_message_count_to_compress`: 15
- `message_retention_count`: 10 (raw messages kept)
- `context_reset_trigger_ratio`: 0.92 (92% triggers reset)

### Compression Profiles

- `conservative`: Minimal compression, preserve detail
- `balanced`: Default mode
- `aggressive`: Heavy compression for long sessions
- `extreme`: Maximum compression for critical situations

### Validation Commands

```bash
# Send test message to orchestrator mailbox
uv run python -c "
import redis, json, uuid
from datetime import datetime, timezone
r = redis.Redis(host='localhost', port=6379, decode_responses=True)
chat_id = 'test-' + str(uuid.uuid4())[:8]
payload = {'chat_id': chat_id, 'messages': [{'type': 'human', 'data': {'content': 'Hello!', 'type': 'human', 'id': str(uuid.uuid4())}}]}
envelope = {'timestamp': datetime.now(timezone.utc).isoformat(), 'sender': 'human', 'recipient': 'orchestrator', 'message': 'Hello!', 'payload': json.dumps(payload)}
r.xadd('qc:mailbox/orchestrator', envelope)
print(f'Sent to chat_id: {chat_id}')
"

# Monitor context compression logs
docker compose logs orchestrator-runtime 2>&1 | grep -E "(context|segment|compression|summary)"

# Check mailbox responses
redis-cli XREAD COUNT 10 STREAMS qc:mailbox/human 0
```

### UI Quality (Last Grunted: 2/5/2026)

- `@st.fragment(run_every=N)` replaces all blocking `time.sleep()` loops (Chat, Mailbox Monitor, Dashboard)
- Python 3.12+ built-in type annotations throughout (`dict`, `list`, `X | None`)
- `@st.cache_resource` for Redis client, `@st.cache_data(ttl=…)` for API responses
- Background `PollingThread` with `threading.Event` for efficient Redis XREAD message detection
- Proper `[build-system]` in `pyproject.toml` (hatchling) for installable package
- Workspace descriptor key consistency (`qc:workspace:descriptors:` prefix)
- All error handlers use specific exceptions where possible; `logger.exception()` for unexpected errors
- No `time.sleep()` blocking the Streamlit event loop anywhere

### Runtime Quality (Last Grunted: 2/5/2026)

- All LLM calls use native `ainvoke()` async (no `asyncio.to_thread` wrappers)
- Pydantic v2 patterns (`model_dump()` over deprecated `.dict()`)
- Lazy `%`-style logging throughout (no f-string overhead at disabled levels)
- Type annotations on all public functions
- LangGraph 2025/2026 patterns: `StateGraph`, `TypedDict` state, `add_messages` reducer, `AsyncPostgresSaver` checkpointing (via `DATABASE_URL`) with `MemorySaver` fallback

### Comparison with LangGraph/LangMem (2025)

| Feature | Quadracode | LangGraph/LangMem |
|---------|------------|-------------------|
| Short-term memory | LangGraph checkpoints (PostgreSQL via `AsyncPostgresSaver`) + message trimming | Checkpointer + thread-scoped state |
| Long-term memory | Episodic + Semantic patterns | Store API + LangMem SDK |
| Compression | LLM-based ContextReducer | Summarization nodes |
| Reset mechanism | ContextResetAgent (disk artifacts) | Manual checkpoint management |
| Observability | TimeTravelRecorder + MetaObserver | LangSmith tracing |
| Externalization | `/shared/context_memory` | BaseStore with vector search |

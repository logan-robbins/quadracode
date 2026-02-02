# Quadracode

**An always-on, LangGraph-native orchestration platform for asynchronous, long-running AI workloads.**

Quadracode is a production-ready framework for deploying resilient AI agent systems that span minutes, hours, or days. It features dynamic fleet management, persistent workspace environments, and full observability.

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

## 🏗 System Components

### Orchestrator (`quadracode-orchestrator`)
The brain of the system. It consumes tasks from `qc:mailbox/orchestrator`, maintains conversation state via LangGraph checkpoints, and dynamically spawns agent containers to handle burst workloads or specialized tasks. It never blocks; it polls for results and emits incremental updates.

### Agents (`quadracode-agent`)
Specialized workers spawned on-demand. They are **purely remote drivers**.
*   **No Local Execution**: Agents do NOT run code in their own containers.
*   **Remote Control**: They use tools to drive the `quadracode-workspace` container.
*   **Ephemeral**: Agents can be destroyed at any time without losing project state (which lives in the workspace).

### Workspace Engine (`quadracode-workspace`)
A strict, sandboxed Docker environment where all actual work happens.
*   **/workspace**: The execution root. Ephemeral builds, tests, and code execution happen here.
*   **/shared**: High-speed volume mounted RW to all agents. used for passing large artifacts between agents.
*   **Default Workspace**: A `workspace-default` service is always available for general tasks.

![Default Workspace](file:///Users/loganrobbins/.gemini/antigravity/brain/25728b2f-838e-4bb8-a5b4-79123836abde/default_workspace_fixed_success_1769918978649.png)

### Control Plane (`quadracode-ui`)
A Streamlit application for full observability.
*   **Chat**: Real-time interaction with background polling.
*   **Mailbox Monitor**: Regex-filtered view of the Redis Stream event fabric.
*   **Workspace Browser**: File explorer for the Docker volumes.

![Workspace Details](file:///Users/loganrobbins/.gemini/antigravity/brain/25728b2f-838e-4bb8-a5b4-79123836abde/workspace_details_success_1769917172483.png)

### Agent Registry (`quadracode-agent-registry`)
FastAPI service (Port 8090) that provides service discovery. It tracks active agents, their capabilities, and healthy heartbeats.

---

## 🛠 Development Commands

**Local UI Development:**
```bash
uv sync
QUADRACODE_MOCK_MODE=true uv run streamlit run quadracode-ui/src/quadracode_ui/app.py
```

**Run Tests:**
```bash
# Infrastructure smoke tests
uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v

# Full E2E suite (requires running stack)
uv run pytest tests/e2e_advanced -m e2e_advanced
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
| **Checkpointer** | `quadracode-runtime/src/quadracode_runtime/graph.py` | LangGraph MemorySaver (dev) / AsyncSqliteSaver (prod) |

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

### Comparison with LangGraph/LangMem (2025)

| Feature | Quadracode | LangGraph/LangMem |
|---------|------------|-------------------|
| Short-term memory | LangGraph checkpoints + message trimming | Checkpointer + thread-scoped state |
| Long-term memory | Episodic + Semantic patterns | Store API + LangMem SDK |
| Compression | LLM-based ContextReducer | Summarization nodes |
| Reset mechanism | ContextResetAgent (disk artifacts) | Manual checkpoint management |
| Observability | TimeTravelRecorder + MetaObserver | LangSmith tracing |
| Externalization | `/shared/context_memory` | BaseStore with vector search |

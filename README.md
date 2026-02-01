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

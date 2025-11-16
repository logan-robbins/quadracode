# Technical Implementation Deep Dive

This document provides a detailed analysis of the Quadracode codebase, mapping the high-level concepts to their concrete implementations across the various services. It is intended to be the definitive source of truth for AI Code Agents working on this codebase.

## 1. High-Level Architecture & Communication

The Quadracode system is a distributed, service-oriented architecture composed of several specialized Python packages. The core communication backbone is a Redis Streams-based message bus, ensuring asynchronous and durable messaging between components.

### 1.1. Event Fabric (Redis Streams)

- **Architecture**: Redis Streams-based messaging. All services communicate by writing to and reading from `qc:mailbox:<recipient_id>` streams.
- **Message Contract (`MessageEnvelope`)**: A Pydantic model in `quadracode-contracts` defines the canonical message structure with fields for `sender`, `recipient`, `timestamp`, and a JSON-serialized `payload`. This enforces type-safe, validated communication across the system.

### 1.2. Service Roles

- **`quadracode-orchestrator`**: The central "brain" responsible for task decomposition, agent lifecycle management, and high-level strategy.
- **`quadracode-agent`**: A worker process that executes tasks assigned by the orchestrator. It has a minimal profile focused on tool execution within a sandboxed workspace.
- **`quadracode-agent-registry`**: A FastAPI service providing discovery and health monitoring for agents. It maintains a roster of available agents in an SQLite database.
- **`quadracode-runtime`**: The foundational library shared by the orchestrator and agents. It contains the core LangGraph state machine, the Perpetual Refinement Protocol (PRP), context engineering components, and time-travel debugging capabilities.
- **`quadracode-tools`**: A collection of LangChain tools that agents use to interact with the world (e.g., execute code, manage files, manage other agents).
- **`quadracode-ui`**: A Streamlit-based web interface for human operators to interact with the orchestrator, monitor streams, and manage autonomous tasks.
- **`scripts/`**: A set of operational shell scripts for managing the infrastructure (e.g., spawning agents, cleaning up resources), primarily invoked by the orchestrator.

## 2. Agent Lifecycle Management

Agent lifecycle (spawning, deletion) is managed directly by the **orchestrator**, which has privileged access to the host's container runtime.

1.  **Orchestrator Decides**: The orchestrator's logic determines a new agent is needed.
2.  **Tool Call**: It invokes the `agent_management_tool` from the `quadracode-tools` package with the `spawn_agent` operation.
3.  **Script Execution**: The tool's Python code, running inside the orchestrator's container, executes the `scripts/agent-management/spawn-agent.sh` shell script.
4.  **Docker Socket Access**: The `orchestrator-runtime` container is deployed with the host's Docker socket mounted (`/var/run/docker.sock`). This allows the shell script to execute `docker` commands against the host's Docker daemon.
5.  **Agent Spawns**: A new agent container is created on the host.
6.  **Registration**: The newly spawned agent, on startup, registers itself with the `quadracode-agent-registry` service.
7.  **Discovery**: The orchestrator can then discover the new agent by querying the registry.

Deletion follows a similar path, with the orchestrator calling the `delete-agent.sh` script.

## 3. `quadracode-runtime`: The Core Engine

The `quadracode-runtime` package is the foundational layer, implementing the core architectural patterns.

### 3.1. State Management (`QuadraCodeState`)

- **Structure**: A central `TypedDict` (`QuadraCodeState`) aggregates state from multiple domains: runtime status, context engine metrics, PRP state, memory, and observability logs. Pydantic models are used for nested structures to balance performance and validation.
- **Serialization**: A comprehensive serialization/deserialization mechanism handles the conversion of the state to and from JSON, managing Pydantic models, enums, and LangChain message types. This is critical for checkpointing and time-travel debugging.

### 3.2. Perpetual Refinement Protocol (PRP)

The PRP is a meta-cognitive loop that enables the system to recover from failure and refine its approach.

- **State Machine (`PRPStateMachine`)**: A finite state machine with states like `HYPOTHESIZE`, `EXECUTE`, `TEST`, and `PROPOSE`. Transitions are governed by guards that check the system's `ExhaustionMode`.
- **Exhaustion Triggers**: The PRP is activated when the system enters a state of "Exhaustion" (e.g., `TEST_FAILURE`, `TOOL_BACKPRESSURE`), forcing it to step back and re-evaluate its strategy.
- **Refinement Ledger**: Every PRP cycle is recorded in a `refinement_ledger`, creating an immutable audit trail of hypotheses, outcomes, and novelty scores. This ledger is a key input for future decision-making.
- **HumanClone Trigger**: The PRP can be initiated by a "rejection" from the `HumanClone` agent, which simulates skeptical human feedback and forces a new refinement cycle.

### 3.3. Time-Travel Debugging

- **Recorder (`TimeTravelRecorder`)**: A thread-safe, singleton class that logs every significant state transition, tool call, and event to a per-thread `.jsonl` file.
- **Deterministic Replay**: The append-only logs allow for the complete, deterministic replay and analysis of any agent's execution history, which is invaluable for debugging complex autonomous behaviors. A CLI tool is provided for replaying and diffing cycles.

### 3.4. Other Key Components

- **Context Engine**: A sophisticated system for dynamically managing the LLM's context window, including progressive loading, curation, and quality scoring.
- **Exhaustion Predictor**: A scikit-learn based logistic regression model that predicts the probability of upcoming exhaustion based on ledger history, allowing for preemptive strategy changes.
- **Workspace Integrity Manager**: A tool for creating checksummed snapshots of agent workspaces, allowing for drift detection and automated restoration to a known-good state.

## 4. Service-Specific Implementations

### 4.1. `quadracode-orchestrator`

- **Role**: High-level task coordination and fleet management.
- **Profile System**: Dynamically selects a system prompt based on the operational mode (`autonomous` vs. supervised). The autonomous prompt is a detailed "operational handbook" mandating specific tools (`hypothesis_critique`, `run_full_test_suite`) at different stages of the decision loop.
- **HumanClone Prompt**: Instructs the `HumanClone` persona to be "relentlessly skeptical" and to format its rejections as a structured `HumanCloneTrigger` JSON object, which kicks off the PRP.

### 4.2. `quadracode-agent`

- **Role**: A simple, task-oriented worker.
- **Profile**: Its profile is minimal, containing a system prompt that instructs it to follow the orchestrator's commands and confine all its work to the provided workspace. It is effectively a "tool" wielded by the orchestrator.

### 4.3. `quadracode-agent-registry`

- **Framework**: A lightweight FastAPI application backed by SQLite.
- **Functionality**: Provides REST endpoints for agent registration (`/agents/register`), liveness heartbeats (`/agents/{id}/heartbeat`), and discovery (`/agents`).
- **Health Monitoring**: Tracks the `last_heartbeat` of each agent and considers an agent "unhealthy" if it has not checked in within a configurable `agent_timeout`.
- **Hotpath Agents**: Includes a `hotpath` flag to protect critical, non-scalable agents (like a long-running debugger) from being accidentally deleted by automated management tools.

### 4.4. `quadracode-tools`

- **Bridge Pattern**: This package acts as a bridge, providing a clean Python tool interface to the LLM while encapsulating the execution of underlying shell scripts for platform-specific operations.
- **`agent_management_tool`**: The key tool used by the orchestrator. It validates its inputs using a Pydantic model (`AgentManagementRequest`) and then calls the appropriate shell script (`spawn-agent.sh`, `delete-agent.sh`, etc.) via `subprocess.run`. It checks the `hotpath` status of an agent via the registry API before allowing deletion.
- **Workspace Tools**: A suite of tools (`workspace_create`, `workspace_exec`, etc.) that provide a sandboxed execution environment for agents using Docker containers.
- **Testing Tools**: Includes `run_full_test_suite` for running test suites within a workspace and `generate_property_tests` for dynamically creating Hypothesis-based tests.

### 4.5. `quadracode-ui`

- **Framework**: A Streamlit web application.
- **Architecture**: Acts purely as a Redis client. It sends user messages to the orchestrator's mailbox and listens for responses on the human's mailbox. A background thread performs a blocking `XREAD` on the Redis stream to listen for new messages, triggering a UI refresh when one arrives.
- **Functionality**: Provides a chat interface, a raw stream viewer for debugging, dashboards for context and autonomous mode metrics, and controls for managing workspaces.

### 4.6. `scripts/`

- **Role**: The operational backend for the `agent_management_tool`. These are shell scripts that contain the raw `docker` or `kubectl` commands.
- **Platform Abstraction**: Each script uses a `case` statement on the `AGENT_RUNTIME_PLATFORM` environment variable to decide whether to execute Docker or Kubernetes commands, allowing the same tool call to work in different environments.
- **JSON I/O**: The scripts are designed to accept command-line arguments and produce structured JSON on `stdout`, making them reliable for programmatic use by the Python tool wrappers.

## 5. Testing Framework

Quadracode uses a comprehensive end-to-end testing framework located in `tests/e2e_advanced/`.

### 5.1. Smoke Tests

- **Duration**: <5 minutes
- **Purpose**: Quick infrastructure validation without requiring full LLM integration
- **Execution**: `uv run pytest tests/e2e_advanced/test_foundation_smoke.py -v`
- **Coverage**: Utilities, metrics collection, logging framework, timeout management, checkpoint persistence, workspace volume inheritance
- **Test File**: `test_foundation_smoke.py`

### 5.2. Advanced E2E Tests (`tests/e2e_advanced/`)

A comprehensive, long-running test suite designed to validate the complete Quadracode system under realistic workloads with real LLM calls.

**Test Modules:**

1. **Foundation Tests** (`test_foundation_long_run.py`): Sustained message flows and multi-agent routing over 30+ conversation turns
2. **Context Engine Stress** (`test_context_engine_stress.py`): Progressive loading, curation, and externalization under high load with large files
3. **PRP and Autonomous Mode** (`test_prp_autonomous.py`): HumanClone rejection cycles, PRP state machine transitions, and autonomous checkpoints
4. **Fleet Management** (`test_fleet_management.py`): Dynamic agent spawning/deletion and hotpath protection mechanisms
5. **Workspace Integrity** (`test_workspace_integrity.py`): Multi-workspace isolation and integrity snapshot/restore functionality
6. **Observability** (`test_observability.py`): Time-travel logging capture and comprehensive metrics stream coverage

**Infrastructure Components:**

- **Metrics Collection** (`utils/metrics_collector.py`): Captures false-stops, HumanClone effectiveness, PRP cycles, and resource utilization
- **LLM-as-a-Judge** (`utils/llm_judge.py`): Semantic classification of orchestrator proposals and HumanClone decisions
- **Test Utilities**: Logging framework, Redis helpers, artifact capture, agent management helpers, timeout wrappers
- **Reporting Scripts**: Aggregate metrics, generate markdown reports, create visualizations (false-stop rates, HumanClone ROC curves, PRP distributions)

**Key Metrics Tracked:**

- **False-Stop Detection**: Count, rate, detection effectiveness, recovery times
- **HumanClone Effectiveness**: Precision, recall, F1 score, latency, exhaustion mode distribution
- **PRP Efficiency**: Cycle counts, state distribution, transition patterns, novelty scores
- **Resource Utilization**: Token usage, costs, tool calls, context overflow events

**Execution:**

```bash
# Run all advanced tests (60-90 minutes)
uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO

# Run specific module
uv run pytest tests/e2e_advanced/test_prp_autonomous.py -v

# Generate reports after test runs
uv run python tests/e2e_advanced/scripts/aggregate_metrics.py \
  --input "tests/e2e_advanced/metrics/*.json" \
  --output tests/e2e_advanced/reports/aggregate_report.json
```

**Design Principles:**

- All tests use real LLM calls (no mocks or stubs)
- Start by running `docker compose up -d redis redis-mcp agent-registry orchestrator-runtime agent-runtime` and verify health via `docker compose ps --services --filter "status=running"`.
- Long-running scenarios (5-20 minutes per test)
- Verbose audit trails with timestamped logs
- Detailed assertion messages for AI coding agents
- Comprehensive metrics exported to JSON

The advanced E2E suite validates the system's ability to handle false-stops, recover from failures through PRP, manage context under load, execute autonomous tasks with checkpoints, dynamically scale the agent fleet, maintain workspace isolation, and provide complete observability through time-travel logs and metrics streams.

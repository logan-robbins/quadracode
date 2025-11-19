# Quadracode Orchestrator Implementation

The `quadracode-orchestrator` is a central component of the Quadracode ecosystem, responsible for high-level task coordination and agent fleet management. It is built upon the shared `quadracode-runtime` but is specialized for its orchestration role through a unique profile and a set of detailed system prompts.

## Architectural Role

The orchestrator acts as the "brain" of the Quadracode system. It receives tasks from the user, decomposes them into smaller, manageable steps, and dispatches those steps to the appropriate agents. It is also responsible for managing the lifecycle of agents, provisioning shared workspaces, and ensuring that tasks are completed efficiently and correctly. The orchestrator's behavior is highly configurable and can be switched between a default, human-supervised mode and a fully autonomous mode.

## Core Components

### 1. Profile (`profile.py`)

The orchestrator's runtime behavior is defined by its profile. The `profile.py` module dynamically configures this profile by selecting the appropriate system prompt based on whether autonomous mode is enabled. It loads the base "orchestrator" profile from the `quadracode_runtime` and injects the selected system prompt. This `PROFILE` object is the central configuration point for the orchestrator's runtime environment.

### 2. LangGraph (`graph.py`)

The `graph.py` module constructs the orchestrator's primary operational workflow using the `build_graph` utility from the `quadracode_runtime`. The behavior of the resulting LangGraph is determined by the system prompt from the orchestrator's `PROFILE`, which guides its decision-making and tool usage.

### 3. System Prompts (`prompts/`)

The orchestrator's behavior is heavily influenced by a set of detailed system prompts, which are organized into the following modules:

- **`system.py`**: This module contains the default system prompt for the orchestrator. It outlines its core capabilities, including agent and workspace management, and is used when the system is in its default, human-supervised mode.

- **`autonomous.py`**: This module defines the system prompt for the orchestrator when operating in autonomous ("HUMAN_OBSOLETE") mode. This prompt provides a comprehensive set of instructions for managing long-running, multi-step tasks without human intervention. It covers the autonomous decision loop, fleet management, and protocols for quality and safety.

- **`human_clone.py`**: This module defines the system prompt for the HumanClone agent, a specialized component that acts as a skeptical taskmaster. The HumanClone's role is to provide relentless, abstract pressure on the orchestrator, ensuring that work is completed to a high standard. This prompt is a key part of the "Plan-Refine-Play" (PRP) loop in autonomous mode.

### 4. Entry Point (`__main__.py`)

The `__main__.py` module serves as the entry point for running the orchestrator as a standalone service. It uses the `run_forever` function from the `quadracode_runtime` to start the orchestrator's asynchronous event loop, allowing it to operate as a persistent, message-driven service.

### 5. Context Engine Integration (`quadracode_runtime`)

Although the context engine is implemented in the shared `quadracode-runtime` package, it is a critical part of how the orchestrator graph behaves at runtime.

- **Location & Primary Types**
  - Core implementation lives in `quadracode_runtime/nodes/context_engine.py` as the `ContextEngine` class.
  - Configuration is defined in `quadracode_runtime/config/context_engine.py` via the `ContextEngineConfig` dataclass, constructed from `ContextEngineConfig.from_environment()`.
  - The orchestrator graph wires this into the LangGraph in `quadracode_runtime/graph.py`, which adds the following nodes:
    - `context_pre` → `ContextEngine.pre_process_sync`
    - `context_governor` → `ContextEngine.govern_context_sync`
    - `context_post` → `ContextEngine.post_process_sync`
    - `context_tool` → `ContextEngine.handle_tool_response_sync`
  - These nodes sit between the orchestrator `driver` node and the `tools` node, so every turn flows through the context engine both before and after tool/driver execution.

- **State Contract (`QuadraCodeState`)**
  - The context engine operates over the shared `QuadraCodeState` type in `quadracode_runtime/state.py`.
  - Key fields it reads/writes include:
    - `messages` (LangChain message list), `context_segments`, `context_window_used`, `context_window_max`.
    - `context_quality_score` and `context_quality_components` (scorer output).
    - `exhaustion_mode`, `exhaustion_probability` (exhaustion predictor output).
    - `refinement_ledger`, `hypothesis_cycle_metrics`, `metrics_log` (PRP / metrics coupling).
    - `workspace` and `workspace_snapshots` (used for workspace integrity).
  - The engine is designed as a pure(ish) transformation over `QuadraCodeState`: each stage (`pre_process`, `govern_context`, `post_process`, `handle_tool_response`) takes a state dict and returns a new state dict, which is then threaded through the LangGraph edges.

- **Configuration & Environment Overrides**
  - `ContextEngineConfig` provides strongly-typed defaults for token limits, quality thresholds, compression, skills discovery, and observability:
    - Token/window controls: `context_window_max`, `target_context_size`, `progressive_batch_size`, `prefetch_depth`.
    - Quality/selection: `quality_threshold`, `min_segment_priority`, `scoring_weights`.
    - Reducer: `reducer_model`, `reducer_chunk_tokens`, `reducer_target_tokens`.
    - Governor: `governor_model`, `governor_max_segments`.
    - Externalization / MemAct: `external_memory_path`, `max_checkpoints`, `checkpoint_frequency`, `externalize_write_enabled`.
    - Metrics: `metrics_enabled`, `metrics_stream_key`, `metrics_redis_url`, `metrics_emit_mode`, `autonomous_metrics_stream_key`.
  - `ContextEngineConfig.from_environment()` applies overrides via env vars, for example:
    - `QUADRACODE_CONTEXT_WINDOW_MAX`, `QUADRACODE_TARGET_CONTEXT_SIZE`, `QUADRACODE_MAX_TOOL_PAYLOAD_CHARS`.
    - `QUADRACODE_QUALITY_THRESHOLD`, `QUADRACODE_GOVERNOR_MAX_SEGMENTS`.
    - `QUADRACODE_REDUCER_MODEL`, `QUADRACODE_GOVERNOR_MODEL`.
    - `QUADRACODE_METRICS_ENABLED`, `QUADRACODE_METRICS_EMIT_MODE`, `QUADRACODE_METRICS_REDIS_URL`, `QUADRACODE_METRICS_STREAM_KEY`.
    - `QUADRACODE_AUTONOMOUS_STREAM_KEY` for routing autonomous events into Redis.
  - For AI coding agents, this means **behavioral tuning for the orchestrator’s context layer is centralized in `ContextEngineConfig` and corresponding env vars**, not in the orchestrator package itself.

- **Execution Flow & Responsibilities**
  - **`pre_process` stage**:
    - Ensures state defaults, enforces hotpath residency against the Agent Registry (`AGENT_REGISTRY_URL`), and computes `context_quality_score` via `ContextScorer`.
    - If quality is below `quality_threshold` or the window overflows `target_context_size`, it invokes `ContextCurator` to prune/reshape segments and recomputes usage.
    - Delegates to `ProgressiveContextLoader` to pull in new segments from skills/docs/code (driven by `ContextEngineConfig.skills_paths`, `project_root`, and `documentation_paths`).
    - Enforces token limits and emits structured metrics via `ContextMetricsEmitter`, then records a time-travel snapshot and observability hooks.
  - **`govern_context` stage**:
    - Applies a governor policy (`governor_model`) over the current context + PRP state to decide whether to keep curating, to externalize memory, or to adjust strategy.
    - Uses `ExhaustionPredictor` to update `exhaustion_mode`/`exhaustion_probability` and may trigger exhaustion events that are logged into time-travel and metrics streams.
  - **`post_process` stage**:
    - Reflects on the driver’s decision (via `_reflect_on_decision`) and appends a dense `reflection_log` entry with issues, recommendations, focus metric, and quality score.
    - Evolves the playbook (`_evolve_playbook`), runs post-decision curation, and optionally writes external memory checkpoints when `_should_checkpoint` returns true.
    - Emits post-process metrics and additional time-travel snapshots for replay/debugging.
  - **`handle_tool_response` stage**:
    - Normalizes and routes tool messages (e.g., PRP tooling, tests, workspace operations) back into the state, updating refinement ledgers, error history, metrics, and long-term memory (`update_memory_guidance`).

- **Workspace Integrity & Non-Blocking Snapshots**
  - The context engine is also the coordination point for workspace integrity when exhaustion events fire:
    - `_update_exhaustion_mode` calls `_handle_workspace_integrity_event` whenever `exhaustion_mode` changes away from `NONE`.
    - `_handle_workspace_integrity_event` uses `validate_workspace_integrity` and `capture_workspace_snapshot` from `quadracode_runtime/workspace_integrity.py` to:
      - Compare the current workspace (`state["workspace"]`) against the last snapshot.
      - Auto-restore if drift is detected and `auto_restore=True`.
      - Append validation state and snapshot metadata into `workspace_validation`, `workspace_snapshots`, and `metrics_log`.
    - To remain safe under ASGI and LangGraph Dev UI, these filesystem-intensive operations are executed via `asyncio.to_thread(...)`, ensuring they do **not** block the orchestrator’s event loop while still providing strong integrity guarantees.
  - Independently, the PRP trigger node (`quadracode_runtime/nodes/prp_trigger.py`) also captures snapshots on HumanClone-driven rejections; those snapshot calls are likewise dispatched through `asyncio.to_thread` to avoid blocking.

- **Observability & Time-Travel**
  - The context engine is tightly integrated with observability facilities:
    - Emits dense, structured events to Redis streams via `ContextMetricsEmitter` (e.g., `pre_process`, `post_process`, `exhaustion_update`, workspace validation events).
    - Uses `get_meta_observer()` from `quadracode_runtime/observability.py` to publish high-level autonomous events (e.g., exhaustion transitions, hotpath residency violations).
    - Streams snapshots and transitions into the time-travel logger (`get_time_travel_recorder()`), which writes JSONL logs under `./time_travel_logs` for replay and diff tooling.
  - From the orchestrator’s perspective, **all of the above is encapsulated inside the context engine nodes** — the orchestrator graph simply wires those nodes in and passes `QuadraCodeState` through them each cycle.

## Conclusion

The `quadracode-orchestrator` is a sophisticated and highly configurable component that is central to the operation of the Quadracode system. Its ability to dynamically switch between human-supervised and fully autonomous modes, combined with its powerful agent and workspace management capabilities, makes it a flexible and robust solution for coordinating complex, multi-agent workflows.

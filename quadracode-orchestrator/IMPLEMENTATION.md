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
    - `context_pre` → `ContextEngine.pre_process`
    - `context_governor` → `ContextEngine.govern_context`
    - `context_post` → `ContextEngine.post_process`
    - `context_tool` → `ContextEngine.handle_tool_response`
  - These are asynchronous `ContextEngine` methods executed by LangGraph’s async runtime; thin `*_sync` wrappers remain available for non-LangGraph entry points.
  - These nodes sit between the orchestrator `driver` node and the `tools` node, so every turn flows through the context engine both before and after tool/driver execution.

- **State Contract (`QuadraCodeState`)**
  - The context engine operates over the shared `QuadraCodeState` type in `quadracode_runtime/state.py`.
  - **Key fields it reads/writes include:**
    - `messages` (LangChain message list) - conversation history with intelligent retention
    - `context_segments` - **single source of truth** for all in-context data (segments include conversation summaries, tool outputs, code search results, etc.)
    - `context_window_used`, `context_window_max` - token budget tracking
    - `context_quality_score` and `context_quality_components` - LLM or heuristic-based quality evaluation
    - `exhaustion_mode`, `exhaustion_probability` - exhaustion predictor output
    - `refinement_ledger`, `hypothesis_cycle_metrics`, `metrics_log` - PRP / metrics coupling
    - `workspace` and `workspace_snapshots` - workspace integrity tracking
    - `llm_stop_detected`, `llm_resume_hint` - top-level flags for exhaustion handling (moved from working_memory)
  - **Deprecated/Removed fields:**
    - `working_memory` dict - eliminated as redundant (use helper functions: `get_segment()`, `get_segment_content()`, `upsert_segment()`, `remove_segment()`)
    - `conversation_summary` string - eliminated as redundant (now stored as `context_segments` entry with id `"conversation-summary"`)
  - The engine is designed as a pure(ish) transformation over `QuadraCodeState`: each stage (`pre_process`, `govern_context`, `post_process`, `handle_tool_response`) takes a state dict and returns a new state dict, which is then threaded through the LangGraph edges.

- **Configuration & Environment Overrides**
  - `ContextEngineConfig` provides strongly-typed defaults for token limits, quality thresholds, and compression, now centered around a token-driven budget model.
  - **Key Controls**:
    - `QUADRACODE_CONTEXT_WINDOW_MAX`: The absolute maximum token limit of the model.
    - `QUADRACODE_OPTIMAL_CONTEXT_SIZE`: The target "healthy" size for the entire context (static prompt + dynamic messages/segments). Compression is triggered when this is exceeded.
    - `QUADRACODE_MESSAGE_BUDGET_RATIO`: The percentage of the *optimal dynamic space* to allocate for conversation history.
    - `QUADRACODE_MIN_MESSAGE_COUNT_TO_COMPRESS`: Compress history if message count > this **OR** tokens exceed budget (defaults to 15).
    - `QUADRACODE_MESSAGE_RETENTION_COUNT`: Always keep the last N messages intact (not summarized) so LLM can see recent context (defaults to 10).
  - **LLM-Driven Context Management** (new):
    - `QUADRACODE_CURATOR_MODEL`: Model for intelligent operation selection (retain/compress/summarize/externalize/discard). Defaults to `anthropic:claude-haiku-4-5-20251001`. Set to `"heuristic"` for fast local decisions.
    - `QUADRACODE_SCORER_MODEL`: Model for 6-dimension context quality evaluation. Defaults to `anthropic:claude-haiku-4-5-20251001`. Set to `"heuristic"` for fast local scoring.
    - `QUADRACODE_REDUCER_MODEL`: Model for content summarization (defaults to `claude-haiku`).
    - `QUADRACODE_GOVERNOR_MODEL`: Model for context segment planning (defaults to `"heuristic"`).
  - `ContextEngineConfig.from_environment()` applies overrides for all settings, making the engine's behavior highly tunable via environment variables.

- **Architectural Model: Static vs. Dynamic Context**
  - The engine is now aware of the large, static `system_prompt` and its token cost.
  - It actively manages the **dynamic context** (growing conversation history and engineered segments) to fit within the `available_dynamic_space` (`OPTIMAL_CONTEXT_SIZE` - `system_prompt_size`).
  - This ensures that compression is triggered correctly from the very first turn and that the context budget is managed intelligently.

- **Execution Flow & Responsibilities**
  - **`pre_process` stage**:
    - Calculates the total dynamic token usage (messages + segments).
    - **Intelligent Message Management**:
      - Compression triggers if **EITHER** message count > `min_message_count_to_compress` **OR** tokens exceed budget.
      - Summarizes oldest messages while keeping last N (`message_retention_count`) intact for LLM visibility.
      - Creates/updates `conversation-summary` segment (not a top-level string field).
      - Working memory (summary) is injected into driver's system prompt before all messages.
    - If segment tokens exceed budget after message compression, invokes the `ContextCurator` to optimize segments.
    - **LLM-Driven Curation**: Curator uses LLM (default: claude-haiku) to intelligently decide which operation to apply to each segment based on current focus, context pressure, and content relevance.
    - Performs progressive loading and other setup tasks.
  - **`govern_context` stage**:
    - Applies a governor policy (`governor_model`) over the now-balanced context to plan the next step.
    - Governor can use heuristics (default) or LLM to determine segment ordering and retention.
  - **`post_process` stage**:
    - **LLM-Driven Quality Scoring**: Uses LLM (default: claude-haiku) to evaluate context across 6 dimensions (relevance, coherence, completeness, freshness, diversity, efficiency).
    - Reflects on the driver's decision (via `_reflect_on_decision`) and appends a dense `reflection_log` entry with issues, recommendations, focus metric, and quality score.
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
  - Externalization of large context segments (when `externalize_write_enabled` is set) and time-travel logging both use background threads (`asyncio.to_thread(...)` around synchronous file writes), so context curation and observability remain non-blocking even under heavy I/O.

- **Observability & Time-Travel**
  - The context engine is tightly integrated with observability facilities:
    - Emits dense, structured events to Redis streams via `ContextMetricsEmitter` (e.g., `pre_process`, `post_process`, `exhaustion_update`, workspace validation events).
    - Uses `get_meta_observer()` from `quadracode_runtime/observability.py` to publish high-level autonomous events (e.g., exhaustion transitions, hotpath residency violations).
    - Streams snapshots and transitions into the time-travel logger (`get_time_travel_recorder()`), which writes JSONL logs under `./time_travel_logs` for replay and diff tooling.
  - From the orchestrator’s perspective, **all of the above is encapsulated inside the context engine nodes** — the orchestrator graph simply wires those nodes in and passes `QuadraCodeState` through them each cycle.

## Recent Enhancements

### Context Standardization (November 2025)
- **Eliminated redundancy**: Removed `working_memory` dict and `conversation_summary` string in favor of `context_segments` as the single source of truth.
- **Helper functions**: Added `get_segment()`, `get_segment_content()`, `upsert_segment()`, `remove_segment()` for clean segment access patterns.
- **Improved retention**: Messages are now intelligently retained with configurable "keep last N" logic to ensure LLM always has recent context.
- **Compression trigger**: Uses OR logic (message count > threshold OR tokens > budget) for more flexible compression.

### LLM-Driven Context Management (November 2025)
- **ContextCurator**: Now uses LLM by default (claude-haiku) to make intelligent operation decisions (retain/compress/summarize/externalize/discard) based on segment content, priority, current focus, and context pressure. Heuristic mode available as fast fallback.
- **ContextScorer**: Now uses LLM by default (claude-haiku) to evaluate context quality across 6 dimensions, providing more accurate quality signals than pure heuristics. Heuristic mode available as fast fallback.
- **Configurable**: Both curator and scorer can be switched between LLM and heuristic modes via environment variables for flexibility and cost optimization.

## Conclusion

The `quadracode-orchestrator` is a sophisticated and highly configurable component that is central to the operation of the Quadracode system. Its ability to dynamically switch between human-supervised and fully autonomous modes, combined with its powerful agent and workspace management capabilities, makes it a flexible and robust solution for coordinating complex, multi-agent workflows. The recent integration of LLM-driven context management ensures that the orchestrator maintains high-quality, relevant context while never exhausting available token budgets, providing best-in-class detail retention for complex reasoning tasks.

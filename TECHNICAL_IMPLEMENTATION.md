# Technical Implementation Deep Dive

This document provides a detailed analysis of the Quadracode codebase, mapping the high-level concepts from the `quadracode_paper.md` to their concrete implementations across the various services.

## 1. Core Concepts & `quadracode-runtime`

The `quadracode-runtime` package serves as the foundational layer of the system, implementing the core architectural patterns and state management logic. It is the heart of the Quadracode architecture, providing the mechanisms that enable resilience, reflection, and long-horizon autonomy.

### 1.1. Event Fabric (Redis Streams)

**Architecture:** Redis Streams-based messaging with MCP abstraction layer.

**Message Contract (`MessageEnvelope`):**
- Fields: `timestamp` (ISO8601 UTC), `sender` (str), `recipient` (str), `message` (str), `payload` (Dict[str, Any])
- Serialization: `to_stream_fields()` converts to Redis stream field/value pairs; `payload` JSON-encoded
- Deserialization: `from_stream_fields()` parses stream entry; malformed JSON placed in `_raw` key (poison pill mitigation)
- Mailbox keys: `qc:mailbox/<recipient_id>` via `mailbox_key()` function

**Implementation (`RedisMCPMessaging`):**
- Abstraction: Redis commands (`xadd`, `xrange`, `xdel`) wrapped as LangChain tools via `aget_mcp_tools()`
- Tool cache: Global `_TOOL_CACHE` dict keyed by tool name; lazy initialization on first `create()` call
- Methods:
  - `publish(recipient, envelope)` → `xadd` to `qc:mailbox/<recipient>` stream, returns entry ID
  - `read(recipient, batch_size=10)` → `xrange` with count limit, parses AST literal_eval response
  - `delete(recipient, entry_id)` → `xdel` removes entry from stream
- Error handling: `_parse_stream_response()` handles malformed AST; returns empty list on parse failure

**Rationale:** Redis Streams provides append-only log semantics with persistence until explicit deletion, enabling durable audit trails. MCP abstraction allows runtime to treat messaging as a tool, enabling testing/mocking without code changes.

### 1.2. State Management

**State Structure (`QuadraCodeState` TypedDict):**
- Base: `RuntimeState` with `messages: Annotated[list[AnyMessage], add_messages]`
- PRP fields: `is_in_prp` (bool), `prp_state` (PRPState enum), `prp_cycle_count` (int), `refinement_ledger` (List[RefinementLedgerEntry])
- Exhaustion: `exhaustion_mode` (ExhaustionMode enum), `exhaustion_probability` (float), `exhaustion_recovery_log` (List[Dict])
- Context: `context_window_used` (int), `context_window_max` (int), `context_quality_score` (float), `context_segments` (List[ContextSegment])
- Memory: `working_memory` (Dict), `external_memory_index` (Dict), `memory_checkpoints` (List[MemoryCheckpoint])
- Observability: `time_travel_log` (List[Dict], rotating buffer), `prp_telemetry` (List[Dict]), `metrics_log` (List[Dict])
- Invariants: `invariants` dict with `needs_test_after_rejection`, `context_updated_in_cycle`, `violation_log`, `novelty_threshold`, `skepticism_gate_satisfied`
- Autonomy counters: `autonomy_counters` dict with `false_stop_events`, `false_stop_mitigated`, `false_stop_pending`, `skepticism_challenges`

**Performance Trade-offs:**
- Top-level state: `TypedDict` (no runtime validation) for performance; Pydantic models for nested structures (`RefinementLedgerEntry`, `WorkspaceSnapshotRecord`)
- Rationale: Validation overhead accumulates over long-horizon tasks; nested structures validated on creation/update only

**Serialization (`serialize_context_engine_state` / `deserialize_context_engine_state`):**
- Recursive traversal: Converts Pydantic models via `model_dump(mode="json")`, enums via `.value`, datetimes via `.isoformat()`
- Messages: `message_to_dict()` / `messages_from_dict()` for LangChain message serialization
- Ledger: Normalizes `RefinementLedgerEntry` instances; handles dict/list mixed types
- State restoration: Type coercion for `exhaustion_mode` (str→ExhaustionMode), `prp_state` (str→PRPState) with fallbacks
- Defaults: `deserialize_context_engine_state()` ensures all required fields exist with safe defaults

**State Initialization (`make_initial_context_engine_state`):**
- Returns fully initialized `QuadraCodeState` with all fields set to defaults
- PRP: `prp_state=PRP_STATE_MACHINE.initial_state` (HYPOTHESIZE), `is_in_prp=False`, `prp_cycle_count=0`
- Invariants: `novelty_threshold=0.15`, all boolean flags `False`, empty violation log

### 1.3. Perpetual Refinement Protocol (PRP)

**State Machine (`PRPStateMachine`):**
- States: `HYPOTHESIZE`, `EXECUTE`, `TEST`, `CONCLUDE`, `PROPOSE` (PRPState enum)
- Transitions: Directed graph stored as `Dict[PRPState, Dict[PRPState, PRPTransition]]`
- Validation: `validate_transition(source, target, exhaustion_mode, human_clone_triggered)` → `PRPTransition` or raises `PRPInvalidTransitionError`

**Transition Guards (`PRPTransition`):**
- `allow_if_exhaustion_in`: Set of `ExhaustionMode` values that MUST be present for transition
- `block_if_exhaustion_in`: Set of `ExhaustionMode` values that BLOCK transition
- `requires_human_clone`: Boolean flag; transition only allowed if `human_clone_triggered=True`
- Example: `TEST→CONCLUDE` blocked if `exhaustion_mode==TEST_FAILURE`
- Example: `PROPOSE→HYPOTHESIZE` requires `human_clone_triggered=True`

**Default Transition Graph (`DEFAULT_PRP_TRANSITIONS`):**
- `HYPOTHESIZE→EXECUTE`: Blocked on `RETRY_DEPLETION`, `TOOL_BACKPRESSURE`
- `EXECUTE→TEST`: Blocked on `TOOL_BACKPRESSURE`
- `EXECUTE→HYPOTHESIZE`: Allowed only on `RETRY_DEPLETION`, `TOOL_BACKPRESSURE`, `PREDICTED_EXHAUSTION`
- `TEST→CONCLUDE`: Blocked on `TEST_FAILURE`, `HYPOTHESIS_EXHAUSTED`
- `TEST→HYPOTHESIZE`: Allowed only on `TEST_FAILURE`, `HYPOTHESIS_EXHAUSTED`
- `CONCLUDE→PROPOSE`: Unconditional
- `CONCLUDE→EXECUTE`: Allowed only on `CONTEXT_SATURATION`, `TOOL_BACKPRESSURE`
- `PROPOSE→HYPOTHESIZE`: Requires `human_clone_triggered=True`

**State Transition Application (`apply_prp_transition`):**
- Inputs: `state`, `target_state`, `exhaustion_mode` (optional, defaults to `state.exhaustion_mode`), `human_clone_triggered` (bool), `strict` (bool)
- Process: Validates transition via `PRP_STATE_MACHINE.validate_transition()`; updates `prp_state`, `is_in_prp=True`
- Invariant updates: `HYPOTHESIZE` or `EXECUTE` entry resets `skepticism_gate_satisfied=False`; `PROPOSE→HYPOTHESIZE` increments `prp_cycle_count`, calls `mark_rejection_requires_tests()`
- Telemetry: Logs transition event to `prp_telemetry` list; calls `get_time_travel_recorder().log_transition()`
- Invariant checking: Calls `check_transition_invariants()` post-transition; logs violations to telemetry (non-fatal)

**Refinement Ledger (`RefinementLedgerEntry` Pydantic model):**
- Fields: `cycle_id` (str), `timestamp` (datetime), `hypothesis` (str), `status` (str), `outcome_summary` (str)
- Exhaustion: `exhaustion_trigger` (ExhaustionMode | None), `test_results` (Dict | None)
- Metadata: `strategy` (str | None), `novelty_score` (float | None), `novelty_basis` (List[str]), `dependencies` (List[str])
- Prediction: `predicted_success_probability` (float | None)
- Causal: `causal_links` (List[Dict]), `metadata` (Dict)
- Methods: `formatted_summary()` returns compact string for prompt injection (280 char truncation)

**Ledger Operations (`add_refinement_ledger_entry`):**
- Normalization: Converts dict payloads to `RefinementLedgerEntry`; handles timestamp string→datetime, exhaustion str→enum
- Appends to `state.refinement_ledger` list; maintains chronological order

**HumanClone Trigger Processing (`prp_trigger_check` node):**
- Interception: Checks `state._last_envelope_sender == HUMAN_CLONE_RECIPIENT`; returns early if not
- Parsing: Extracts last `HumanMessage` from `state.messages`; calls `parse_human_clone_trigger(content)` (JSON/YAML with markdown fence stripping)
- State update: Sets `state.human_clone_trigger`, `state.human_clone_requirements`, `state.exhaustion_mode` from trigger
- Transition: Calls `apply_prp_transition(state, PRPState.HYPOTHESIZE, exhaustion_mode, human_clone_triggered=True)`
- Message transformation: Replaces `HumanMessage` with `SystemMessage` (summary) + `ToolMessage` (structured critique record)
- Workspace snapshot: Calls `capture_workspace_snapshot()` with reason="human_clone_rejection"

**Trigger Contract (`HumanCloneTrigger` Pydantic model):**
- Fields: `cycle_iteration` (int, ge=0), `exhaustion_mode` (HumanCloneExhaustionMode enum), `required_artifacts` (List[str]), `rationale` (str | None)
- Validation: `required_artifacts` normalized via `_normalise_artifacts()` validator (handles None, list, single value)

### 1.4. Time-Travel Debugging

**Recorder (`TimeTravelRecorder`):**
- Storage: Thread-local log files at `base_dir/<thread_id>.jsonl` (default: `./time_travel_logs/`)
- Thread safety: Per-file `threading.Lock` stored in `_locks` dict
- Retention: In-memory `time_travel_log` list capped at `retention` (default: 500); oldest entries dropped

**Event Logging API:**
- `log_stage(state, stage, payload, state_update)`: Records stage transitions
- `log_tool(state, tool_name, payload)`: Records tool invocations
- `log_transition(state, event, payload, state_update)`: Records state machine transitions
- `log_snapshot(state, reason, payload)`: Records cycle snapshots

**Event Structure (`_persist`):**
- Metadata: `thread_id`, `cycle_id` (from `_cycle_id_from_state()`), `prp_state`, `exhaustion_mode`, `iteration_count`
- Entry: `timestamp` (ISO8601 UTC), `event`, `payload`, `stage`/`tool` (optional), `state_update` (optional)
- Serialization: `_safe_json_dump()` with custom `_default()` handler for Pydantic models, datetimes

**CLI Tooling (`main` function):**
- `replay --log <path> --cycle <cycle_id>`: Filters entries by `cycle_id`, prints formatted events
- `diff --log <path> --cycle-a <id> --cycle-b <id>`: Compares cycle snapshots; computes deltas for `total_tokens`, `tool_calls`, `stage_usage` length, status changes
- Format: `_format_event()` produces `[timestamp] event :: payload` strings

**File Format:**
- JSON Lines (`.jsonl`): One JSON object per line, UTF-8 encoded
- Append-only: File opened in append mode (`"a"`); lock-protected writes
- Processing: Line-oriented format enables `grep`/`awk`/`jq` processing

## 2. Service-Specific Implementations

### 2.1. `quadracode-orchestrator`

**Graph Construction (`graph.py`):**
- Uses `build_graph(PROFILE.system_prompt)` from runtime
- Profile: `PROFILE` from `profile.py` (dynamically selected prompt)

**Profile Selection (`profile.py`):**
- Mode detection: `is_autonomous_mode_enabled()` checks `QUADRACODE_MODE`, `QUADRACODE_AUTONOMOUS_MODE`, `HUMAN_OBSOLETE_MODE` env vars
- Prompt selection: `AUTONOMOUS_SYSTEM_PROMPT` if autonomous, else `SYSTEM_PROMPT`
- Profile: `replace(load_profile("orchestrator"), system_prompt=_PROMPT)`

**Autonomous Prompt (`prompts/autonomous.py`):**
- Structure: Operational handbook with sections: Mission, Workspace Discipline, Decision Loop, Fleet Management, Autonomous Tools, Routing, Milestones, Quality & Safety, Finalization Protocol, Escalation
- Decision Loop: Explicit "Evaluate → Critique → Plan → Execute" steps with `hypothesis_critique` tool requirement
- Finalization Protocol: Requires `run_full_test_suite` with `overall_status='passed'` before `request_final_review`
- Tool mandates: `autonomous_checkpoint` for milestones, `hypothesis_critique` for iterations, `run_full_test_suite` before completion

**HumanClone Prompt (`prompts/human_clone.py`):**
- Persona: "Relentlessly skeptical"; default response is rejection
- Output format: MUST return JSON matching `HumanCloneTrigger` schema in fenced ```json block
- Validation: Prompt explicitly requires `exhaustion_mode` enum, `required_artifacts` list, `cycle_iteration` integer
- Constraints: Forbidden from using `escalate_to_human` tool; stateless (no memory)

**Autonomous Tool Processing (`autonomous.py`):**
- Tool names: `autonomous_checkpoint`, `request_final_review`, `escalate_to_human`, `hypothesis_critique`, `autonomous_escalate`
- Processing: `process_autonomous_tool_response(state, tool_response)` extracts `ToolMessage`, parses JSON payload
- Events:
  - `checkpoint`: Creates `AutonomousMilestone`, upserts to `state.milestones` list (sorted by milestone number)
  - `final_review_request`: Sets `autonomous_routing` to `HUMAN_CLONE_RECIPIENT`, records test results
  - `escalation`: Creates `AutonomousErrorRecord`, sets routing to human
  - `hypothesis_critique`: Calls `apply_hypothesis_critique()`, updates critique backlog

### 2.2. `quadracode-agent`

**Graph Construction (`graph.py`):**
- Uses `build_graph(PROFILE.system_prompt)` from runtime
- Profile: `PROFILE` from `profile.py` (replaces `AGENT_PROFILE` system prompt)

**Profile (`profile.py`):**
- Base: `AGENT_PROFILE` from runtime (`quadracode_runtime.profiles`)
- Prompt: `SYSTEM_PROMPT` from `prompts/system.py` (simple instruction to follow orchestrator)

**Design Rationale:**
- Minimal responsibilities: Task execution only; no meta-level planning
- Separation: Orchestrator handles "what/why"; agent handles "how"
- Hierarchy: Agent is a "tool" wielded by orchestrator

### 2.3. `quadracode-agent-registry`

**Application (`app.py`):**
- Framework: FastAPI application
- Database: SQLite via `Database` class (path from `RegistrySettings.database_path`)
- Service: `AgentRegistryService(db, settings)` initialized at startup
- Routes: Mounted via `get_router(service)` from `api.py`

**Database Schema (`database.py`):**
- Table: `agents` with columns: `agent_id` (TEXT PRIMARY KEY), `host` (TEXT), `port` (INTEGER), `status` (TEXT), `registered_at` (TEXT ISO8601), `last_heartbeat` (TEXT ISO8601 nullable), `hotpath` (INTEGER, default 0)
- Migrations: `ALTER TABLE` attempts for `hotpath` column (ignored if exists)
- Operations:
  - `upsert_agent()`: `INSERT ... ON CONFLICT DO UPDATE`; preserves existing `hotpath=1` on conflict
  - `update_heartbeat()`: Updates `status` and `last_heartbeat` WHERE `agent_id=?`
  - `delete_agent()`: `DELETE FROM agents WHERE agent_id=?`
  - `fetch_agent()`: `SELECT * FROM agents WHERE agent_id=?`
  - `fetch_agents()`: `SELECT * FROM agents [WHERE hotpath=1] ORDER BY registered_at DESC`
  - `set_hotpath()`: `UPDATE agents SET hotpath=? WHERE agent_id=?`

**Service Logic (`service.py`):**
- Health check: `_is_healthy(agent)` checks `status==HEALTHY` and `last_heartbeat >= now - agent_timeout` (from settings)
- Registration: `register(payload)` calls `db.upsert_agent()`, returns `AgentInfo` with `status=HEALTHY`, `hotpath` from payload
- Heartbeat: `heartbeat(hb)` calls `db.update_heartbeat()`, returns bool
- Listing: `list_agents(healthy_only, hotpath_only)` filters by health/timeout, optionally filters by `hotpath_only` flag
- Hotpath: `set_hotpath(agent_id, hotpath)` updates DB flag, returns updated `AgentInfo`; raises `ValueError("agent_not_found")` if missing
- Deletion: `remove_agent(agent_id, force)` checks `hotpath` flag; raises `ValueError("hotpath_agent")` if hotpath and not forced

**API Contracts (`agent_registry.py`):**
- `AgentRegistrationRequest`: `agent_id`, `host`, `port`; optional `hotpath` (bool)
- `AgentHeartbeat`: `agent_id`, `status` (AgentStatus enum), `reported_at` (datetime)
- `AgentInfo`: All fields from registration + `status`, `registered_at`, `last_heartbeat`, `hotpath`
- `AgentListResponse`: `agents` (List[AgentInfo]), `healthy_only`, `hotpath_only` flags
- `RegistryStats`: `total_agents`, `healthy_agents`, `unhealthy_agents`, `last_updated`

**Hotpath Enforcement:**
- Policy: `hotpath` flag stored in SQLite; checked by `agent_management` tool before deletion
- Workflow: `mark_hotpath` → POST `/agents/{id}/hotpath` → `set_hotpath()` → DB update; `delete_agent` → GET `/agents/{id}` → check `hotpath` → block if true

### 2.4. `quadracode-tools`

**Agent Management Tool (`agent_management.py`):**
- Tool definition: `@tool(args_schema=AgentManagementRequest)` with `agent_management_tool` name
- Operations: `spawn_agent`, `delete_agent`, `list_containers`, `get_container_status`, `mark_hotpath`, `clear_hotpath`, `list_hotpath`

**Request Schema (`AgentManagementRequest`):**
- Fields: `operation` (Literal enum), `agent_id` (str | None), `image` (str | None, default "quadracode-agent"), `network` (str | None, default "quadracode_default"), `workspace_id`, `workspace_volume`, `workspace_mount`
- Validation: `@root_validator` ensures `agent_id` required for delete/status/hotpath ops; `workspace_mount` requires `workspace_volume`

**Script Execution (`_run_script`):**
- Path resolution: `_get_scripts_dir()` checks `QUADRACODE_SCRIPTS_DIR` env, then `scripts/agent-management/` relative to repo root, fallback `/app/scripts/agent-management`
- Execution: `subprocess.run([script_path] + args, capture_output=True, text=True, timeout=30, env=env_overrides)`
- Response: Parses JSON from stdout; returns error dict on JSON decode failure or timeout

**Spawn Agent:**
- Args: `[agent_id?, image?, network?]` (empty string placeholders for optional args)
- Workspace: Reads `QUADRACODE_ACTIVE_WORKSPACE_DESCRIPTOR` env (JSON), extracts `workspace_id`, `volume`, `mount_path`
- Env overrides: `QUADRACODE_WORKSPACE_ID`, `QUADRACODE_WORKSPACE_VOLUME`, `QUADRACODE_WORKSPACE_MOUNT` passed to script
- Script: `spawn-agent.sh` (Docker/Kubernetes platform-specific)

**Delete Agent:**
- Hotpath check: `_is_hotpath_agent(agent_id)` → GET `/agents/{id}` → check `payload.hotpath` boolean
- Blocking: Returns error JSON `{"success": false, "error": "hotpath_agent", "message": "..."}` if hotpath
- Script: `delete-agent.sh` with `agent_id` arg

**Hotpath Operations:**
- `mark_hotpath` / `clear_hotpath`: `_update_hotpath_flag(agent_id, bool)` → POST `/agents/{id}/hotpath` with `{"hotpath": bool}` → returns success/error
- `list_hotpath`: `_list_hotpath_agents()` → GET `/agents/hotpath` → returns `{"success": true, "agents": [...], "count": N}`

**Registry Client (`_registry_request`):**
- Base URL: `_registry_base_url()` from `agent_registry.py` (env `AGENT_REGISTRY_URL` or `http://quadracode-agent-registry:8090`)
- HTTP: `httpx.Client(timeout=REGISTRY_TIMEOUT)` (default 5s)
- Response: `(bool, Any)` tuple; `False` on HTTP error or connection failure

**Bridge Pattern:**
- Abstraction: Tool interface decouples LLM from platform (Docker vs Kubernetes)
- Implementation: Shell scripts handle platform-specific commands (`docker run` vs `kubectl apply`)
- Portability: Infrastructure changes don't require prompt/logic modifications

### 2.5. Additional Runtime Components

**Graph Construction (`graph.py`):**
- Checkpointer: `_build_checkpointer()` tries `SqliteSaver` (path from `_default_checkpoint_path()`), falls back to `MemorySaver` on failure
- Checkpoint path: Checks `QUADRACODE_CHECKPOINT_DB`, `SHARED_PATH/checkpoints.sqlite3`, `/shared/checkpoints.sqlite3`, `.quadracode/checkpoints.sqlite3` (first writable)
- Recursion limit: `GRAPH_RECURSION_LIMIT` from env (default 80)
- Graph structure: `START → prp_trigger_check → context_pre → context_governor → driver → context_post → [tools | END] → context_tool → driver`

**Driver Node (`driver.py`):**
- Model: `init_chat_model("anthropic:claude-sonnet-4-20250514")` or heuristic mode (`QUADRACODE_DRIVER_MODEL=heuristic`)
- System prompt assembly: Base prompt + `governor_prompt_outline` (system/focus/ordered_segments) + `refinement_memory_block` + skills metadata + deliberative plan + memory guidance
- Tool binding: `llm.bind_tools(tools)` before invocation
- Message handling: Prepends `SystemMessage` if missing; replaces existing system message

**Invariants (`invariants.py`):**
- State flags: `needs_test_after_rejection`, `context_updated_in_cycle`, `skepticism_gate_satisfied`
- Violation log: List of dicts with `timestamp`, `code`, `details`
- Transition checks: `check_transition_invariants(state, from_state, to_state)` validates:
  - `test_after_rejection`: Blocks `CONCLUDE`/`PROPOSE` if `needs_test_after_rejection=True` and transition from `PROPOSE→HYPOTHESIZE`
  - `context_update_per_cycle`: Blocks `CONCLUDE`/`PROPOSE` if `context_updated_in_cycle=False`
  - `skepticism_gate`: Blocks `CONCLUDE`/`PROPOSE` if `skepticism_gate_satisfied=False`
- Functions: `mark_rejection_requires_tests()`, `mark_context_updated()`, `clear_test_requirement()`

**Exhaustion Predictor (`exhaustion_predictor.py`):**
- Model: `LogisticRegression(solver="liblinear", max_iter=1000, class_weight="balanced")` from scikit-learn
- Features (12): Total cycles, exhaustion rate, recent exhaustion rate, failure rate, recent failure rate, mean hypothesis length, mean outcome length, outcome length stddev, consecutive exhaustion count, consecutive failure count, time since last exhaustion, success rate
- Training: `fit(ledger)` builds dataset from ledger history (max 128 entries); trains if ≥2 classes
- Prediction: `predict_probability(ledger)` computes features from current history, returns probability [0,1]
- Preemption: `should_preempt(ledger)` returns `predict_probability() >= threshold` (default 0.7)

**Ledger Management (`ledger.py`):**
- Operations: `propose_hypothesis`, `conclude_hypothesis`, `query_past_failures`, `infer_causal_chain`
- Novelty analysis: `_analyze_novelty()` computes token-based Jaccard similarity (0-1); novelty = 1 - max_similarity; blockers if similarity ≥0.7 with failed entry and same strategy
- Success prediction: `_predict_success_probability()` uses historical success rate + similar entries (≥0.6 similarity) + novelty multiplier (0.4 + 0.6*novelty)
- Causal inference: `_build_dependency_graph()` creates NetworkX DiGraph from ledger entries; `infer_causal_chain` computes predecessor relationships with confidence scores (0.55 base, 0.85 for failed predecessors, 0.72 for succeeded)

**Workspace Integrity (`workspace_integrity.py`):**
- Snapshot: `capture_workspace_snapshot()` creates tar.gz archive, SHA256 manifest, aggregate checksum
- Archive: Docker volume mount or host path; tar.gz via `docker run --mount` or Python `tarfile`
- Manifest: List of `{path, size, sha256}` dicts sorted by path
- Checksum: SHA256 of concatenated `path|size|sha256` strings
- Diff: `_generate_manifest_diff()` uses `difflib.unified_diff()` on manifest lines
- Validation: `validate_workspace_integrity()` compares current checksum to reference; optional `auto_restore` via tar extraction
- Storage: `workspace_snapshots` list in state (max 5 entries, oldest dropped)

**Context Engine (`nodes/context_engine.py`):**
- Pipeline: `pre_process` → quality scoring → curation (if below threshold) → progressive loading → exhaustion update → `govern_context` → `driver` → `post_process` → reflection → playbook evolution
- Quality threshold: `config.quality_threshold` (default 0.7); triggers curation if below
- Context limits: `target_context_size` vs `context_window_max`; overflow triggers curation
- Exhaustion prediction: `exhaustion_predictor.predict_probability()` updates `exhaustion_probability`; sets `exhaustion_mode=PREDICTED_EXHAUSTION` if `should_preempt()`
- Hotpath enforcement: `_enforce_hotpath_residency()` probes registry for hotpath agents, logs violations

**Profiles (`profiles.py`):**
- Profile structure: `RuntimeProfile(name, default_identity, system_prompt, policy)`
- Recipient policies: `RecipientPolicy`, `OrchestratorRecipientPolicy`, `AutonomousRecipientPolicy`, `AgentRecipientPolicy`, `HumanCloneRecipientPolicy`
- Autonomous policy: Extracts `AutonomousRoutingDirective` from payload; routes to human only if `deliver_to_human=True` or `escalate=True`
- Profile loading: `load_profile(name)` returns cached or newly constructed profile

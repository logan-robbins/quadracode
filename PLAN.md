# Quadracode Advanced E2E Testing Plan

## Executive Summary

This plan defines a comprehensive, long-running end-to-end testing framework for the Quadracode multi-agent system. The tests will run for a minimum of 5 minutes with real language models, exercising full message flows between HumanClone, Orchestrator, and multiple Agents. All tests must produce verbose, timestamped logs proving inter-service communication occurred. The framework targets AI coding agents as the primary executors, requiring explicit verbosity, generous timeouts, and detailed assertion messages.

## Core Principles

1. **Real LLMs Only**: No stubs, mocks, or simulated responses. Every test invokes Anthropic Claude via the production LangGraph runtime.
2. **Full Docker Stack**: All services (redis, redis-mcp, agent-registry, orchestrator-runtime, agent-runtime, human-clone-runtime) must run in docker-compose as defined in `docker-compose.yml`.
3. **Long-Running Scenarios**: Minimum 5 minutes of sustained interaction with multiple conversation turns.
4. **Verbose Audit Trails**: Every message, tool call, state transition, and PRP cycle must be logged to disk with timestamps and correlation IDs.
5. **AI-Agent-Friendly**: Tests include detailed docstrings, assertion messages, and troubleshooting hints for AI coding agents.

---

## Architecture Overview

### Test Execution Environment

- **Base Directory**: `/Users/loganrobbins/research/quadracode/tests/e2e_advanced/`
- **Log Directory**: `/Users/loganrobbins/research/quadracode/tests/e2e_advanced/logs/` (created per test run with ISO timestamp subdirectories)
- **Artifact Directory**: `/Users/loganrobbins/research/quadracode/tests/e2e_advanced/artifacts/` (Redis snapshots, message traces, PRP ledgers)
- **Fixtures**: Reuse and extend `tests/conftest.py` and `tests/e2e/test_end_to_end.py` utilities

### Docker Compose Services Required

All tests must bring up the following services in order:

1. `redis` (with healthcheck)
2. `redis-mcp` (with healthcheck)
3. `agent-registry` (with healthcheck)
4. `orchestrator-runtime` (the orchestrator's Python runtime)
5. `agent-runtime` (at least one baseline agent)
6. `human-clone-runtime` (the HumanClone agent for PRP triggers)

### Additional Dynamic Agents

Tests must spawn additional agents dynamically using `scripts/agent-management/spawn-agent.sh` to validate fleet scaling, workspace isolation, and multi-agent coordination.

---

## Test Suite Structure

### Module 1: Foundation (`test_foundation_long_run.py`)

**Purpose**: Establish baseline long-running message flows without complex PRP or autonomous mode logic.

#### Test 1.1: Sustained Orchestrator-Agent Ping-Pong (5 minutes)

- **Objective**: Validate message delivery, mailbox polling, and LLM response generation over 30+ conversation turns.
- **Setup**:
  - [ ] Bring up full docker-compose stack
  - [ ] Create log directory: `logs/{test_name}_{timestamp}/`
  - [ ] Initialize Redis stream baselines for `qc:mailbox/orchestrator`, `qc:mailbox/human`, `qc:mailbox/agent-runtime`
  - [ ] Set `SUPERVISOR_RECIPIENT=human`
- **Test Flow**:
  - [ ] Send initial message from human to orchestrator: "Begin a 5-minute conversation. Acknowledge receipt and ask me a question."
  - [ ] Wait for orchestrator response (timeout: 60s)
  - [ ] Log full message envelope (id, timestamp, sender, recipient, message, payload)
  - [ ] Extract AI response text from payload.messages
  - [ ] Assert AI response is non-empty and contains a question
  - [ ] Respond with a simple answer (e.g., "Yes, continue.")
  - [ ] Repeat for 30 turns OR until 5 minutes elapsed (whichever comes first)
  - [ ] Log each turn to `logs/{test_name}_{timestamp}/turn_{N}.json`
  - [ ] Assert total turns >= 30
  - [ ] Assert no message delivery failures (check Redis stream gaps)
- **Verification**:
  - [ ] Parse all turn logs and verify monotonically increasing stream IDs
  - [ ] Assert `qc:context:metrics` stream recorded `pre_process`, `post_process`, `load` events for every turn
  - [ ] Assert orchestrator mailbox has >= 30 entries
  - [ ] Assert human mailbox has >= 30 entries
- **Teardown**:
  - [ ] Dump Redis streams to `artifacts/{test_name}_{timestamp}/redis_streams.json`
  - [ ] Dump orchestrator logs: `docker compose logs orchestrator-runtime > logs/{test_name}_{timestamp}/orchestrator.log`
  - [ ] `docker compose down -v`

#### Test 1.2: Multi-Agent Message Routing (5 minutes)

- **Objective**: Spawn 3 dynamic agents and route messages through orchestrator to each agent, verifying correct mailbox targeting.
- **Setup**:
  - [ ] Bring up full docker-compose stack
  - [ ] Spawn 3 agents with IDs: `agent-worker-1`, `agent-worker-2`, `agent-worker-3` using `spawn-agent.sh`
  - [ ] Wait for all 3 agents to register with agent-registry (poll `/agents` endpoint, timeout: 120s)
  - [ ] Create log directory
- **Test Flow**:
  - [ ] Send message to orchestrator: "List all registered agents using the agent_registry tool."
  - [ ] Wait for response (timeout: 90s)
  - [ ] Assert response contains tool call to `agent_registry` with operation `list`
  - [ ] Assert response mentions all 3 dynamic agent IDs
  - [ ] Send message: "Send a simple 'Hello' message to agent-worker-1 and wait for its response."
  - [ ] Wait for orchestrator to route message to `qc:mailbox/agent-worker-1`
  - [ ] Poll agent-worker-1 mailbox for message arrival (timeout: 60s)
  - [ ] Log agent-worker-1 message receipt
  - [ ] Wait for agent-worker-1 response to orchestrator (timeout: 120s)
  - [ ] Wait for orchestrator to relay response to human (timeout: 60s)
  - [ ] Repeat for agent-worker-2 and agent-worker-3
  - [ ] Total test duration >= 5 minutes (pad with additional round-robin messages if needed)
- **Verification**:
  - [ ] Assert each agent mailbox received exactly 1 message
  - [ ] Assert orchestrator mailbox received 3+ messages (from agents)
  - [ ] Assert human mailbox received 3+ responses (relayed by orchestrator)
  - [ ] Parse agent-registry `/stats` endpoint: `total_agents >= 4` (3 dynamic + 1 baseline)
- **Teardown**:
  - [ ] Delete dynamic agents using `delete-agent.sh` for each ID
  - [ ] Dump all mailbox streams and logs
  - [ ] `docker compose down -v`

---

### Module 2: Context Engine Stress (`test_context_engine_stress.py`)

**Purpose**: Validate context engineering components (progressive loader, curator, governor, scorer) under sustained load with large tool outputs and multiple artifacts.

#### Test 2.1: Progressive Loader Artifact Cascade (7 minutes)

- **Objective**: Trigger progressive loading of test artifacts, workspace snapshots, and code files across 20+ turns.
- **Setup**:
  - [ ] Bring up full stack
  - [ ] Create a workspace using `workspace_create` tool (via orchestrator)
  - [ ] Populate workspace with 5 Python files (total ~10KB code)
  - [ ] Create 3 test artifacts in workspace (pytest output, coverage report, hypothesis log)
  - [ ] Set environment variables to trigger progressive loading: `QUADRACODE_CONTEXT_STRATEGY=progressive`
- **Test Flow**:
  - [ ] Send message: "List all files in the workspace."
  - [ ] Wait for response with workspace_exec output (timeout: 90s)
  - [ ] Log tool call payload size
  - [ ] Send message: "Read the first Python file."
  - [ ] Wait for response (timeout: 90s)
  - [ ] Assert `read_file` tool called
  - [ ] Send message: "Run the test suite using pytest."
  - [ ] Wait for response (timeout: 180s)
  - [ ] Assert `run_full_test_suite` tool called
  - [ ] Send message: "Analyze the test coverage report."
  - [ ] Assert progressive loader loaded test artifact from context
  - [ ] Repeat with variations for 20 turns OR 7 minutes
- **Verification**:
  - [ ] Parse `qc:context:metrics` stream for `load` events
  - [ ] Assert >= 10 `load` events recorded
  - [ ] Assert `segments` field in load payload lists artifact types (test_output, coverage, workspace_snapshot)
  - [ ] Assert `pre_process` events show increasing `input_token_count` over time
  - [ ] Assert `curation` events triggered when context size exceeded threshold
- **Teardown**:
  - [ ] Destroy workspace
  - [ ] Dump context metrics to `artifacts/{test_name}_{timestamp}/context_metrics.json`
  - [ ] `docker compose down -v`

#### Test 2.2: Context Curation and Externalization (8 minutes)

- **Objective**: Force context overflow and verify curator applies MemAct operations (retain, compress, summarize) and externalizes large tool outputs.
- **Setup**:
  - [ ] Bring up full stack
  - [ ] Override environment variables to lower thresholds:
    - `QUADRACODE_TARGET_CONTEXT_SIZE=5000` (tokens)
    - `QUADRACODE_MAX_TOOL_PAYLOAD_CHARS=500`
  - [ ] Create workspace with large code files (total 50KB)
- **Test Flow**:
  - [ ] Send message: "Read all Python files in the workspace."
  - [ ] Wait for orchestrator to call `read_file` multiple times (timeout: 180s)
  - [ ] Assert tool outputs exceed `MAX_TOOL_PAYLOAD_CHARS`
  - [ ] Wait for next turn (send any follow-up message)
  - [ ] Poll `qc:context:metrics` for `curation` event (timeout: 120s)
  - [ ] Assert curation event payload contains `actions` field with operations like `compress`, `externalize`
  - [ ] Repeat for 10+ turns, each triggering curation
- **Verification**:
  - [ ] Assert >= 5 `curation` events in metrics stream
  - [ ] Assert `externalize` action applied to at least 2 tool outputs
  - [ ] Assert `compress` action applied to at least 3 segments
  - [ ] Parse logs for "External artifact stored" messages
  - [ ] Assert total context size stayed below `TARGET_CONTEXT_SIZE * 1.5` in all `post_process` events
- **Teardown**:
  - [ ] Dump context metrics and curation logs
  - [ ] `docker compose down -v`

---

### Module 3: PRP and Autonomous Mode (`test_prp_autonomous.py`)

**Purpose**: Exercise Perpetual Refinement Protocol (PRP) state machine with HumanClone rejection triggers and autonomous mode checkpoints.

#### Test 3.1: HumanClone Rejection Cycle (10 minutes)

- **Objective**: Simulate a task where orchestrator fails tests, HumanClone rejects the work, and PRP forces hypothesis refinement.
- **Setup**:
  - [ ] Bring up full stack including `human-clone-runtime`
  - [ ] Set `QUADRACODE_PROFILE=orchestrator` for orchestrator
  - [ ] Set `QUADRACODE_PROFILE=human_clone` for human-clone-runtime
  - [ ] Create workspace with a failing test suite
  - [ ] Set `SUPERVISOR_RECIPIENT=human_clone` to route responses through HumanClone
- **Test Flow**:
  - [ ] Send message to orchestrator: "Fix the failing test suite in the workspace. You have 10 minutes."
  - [ ] Wait for orchestrator to run tests using `run_full_test_suite` (timeout: 180s)
  - [ ] Assert test suite fails
  - [ ] Wait for orchestrator to send work to human_clone mailbox (timeout: 120s)
  - [ ] Log HumanClone mailbox message
  - [ ] Wait for HumanClone to send rejection trigger (timeout: 180s)
  - [ ] Assert rejection payload contains `HumanCloneTrigger` JSON with `exhaustion_mode` (e.g., `TEST_FAILURE`)
  - [ ] Wait for orchestrator to enter PRP `HYPOTHESIZE` state (poll orchestrator logs for "prp_state: HYPOTHESIZE", timeout: 60s)
  - [ ] Wait for orchestrator to propose new hypothesis (timeout: 180s)
  - [ ] Wait for orchestrator to re-execute and re-test (timeout: 240s)
  - [ ] Assert orchestrator sends updated work to HumanClone
  - [ ] If tests still fail, HumanClone rejects again (up to 3 cycles)
  - [ ] If tests pass, HumanClone accepts (sends approval message)
  - [ ] Total test duration >= 10 minutes
- **Verification**:
  - [ ] Parse orchestrator state dumps (via time-travel logs if enabled)
  - [ ] Assert `prp_state` transitioned through: `EXECUTE -> TEST -> HYPOTHESIZE -> EXECUTE -> TEST -> CONCLUDE -> PROPOSE`
  - [ ] Assert `prp_cycle_count` incremented at least once
  - [ ] Assert `refinement_ledger` in state contains >= 1 hypothesis entry with `outcome` field
  - [ ] Parse `qc:mailbox/orchestrator` for `manage_refinement_ledger` tool calls
  - [ ] Assert HumanClone mailbox received >= 2 messages (initial work + refinement)
- **Teardown**:
  - [ ] Dump `refinement_ledger` to `artifacts/{test_name}_{timestamp}/refinement_ledger.json`
  - [ ] Dump HumanClone logs
  - [ ] `docker compose down -v`

#### Test 3.2: Autonomous Mode Full Lifecycle (15 minutes)

- **Objective**: Run orchestrator in fully autonomous mode with checkpoints, escalations, and final review.
- **Setup**:
  - [ ] Bring up full stack
  - [ ] Set `QUADRACODE_MODE=autonomous` (or equivalent flag)
  - [ ] Set `AUTONOMOUS_MAX_ITERATIONS=50`
  - [ ] Set `AUTONOMOUS_RUNTIME_MINUTES=15`
  - [ ] Create workspace with a simple coding task (e.g., "Write a function to calculate Fibonacci numbers with tests")
- **Test Flow**:
  - [ ] Send message to orchestrator: "Autonomously complete the coding task in the workspace. Checkpoint every 3 minutes."
  - [ ] Wait for orchestrator to acknowledge autonomous mode (timeout: 60s)
  - [ ] Poll `qc:autonomous:events` stream for `checkpoint` events (every ~3 minutes, timeout: 200s per checkpoint)
  - [ ] Assert checkpoint payloads contain `progress_summary` field
  - [ ] Wait for orchestrator to call `run_full_test_suite` (timeout: 300s)
  - [ ] If tests fail, wait for `hypothesis_critique` tool call (timeout: 180s)
  - [ ] Assert critique payload contains `critique_type` and `reasoning` fields
  - [ ] Wait for orchestrator to call `request_final_review` (timeout: 300s)
  - [ ] Assert final review includes test results and coverage metrics
  - [ ] Total test duration >= 15 minutes
- **Verification**:
  - [ ] Assert >= 4 `autonomous_checkpoint` tool calls logged
  - [ ] Assert `qc:autonomous:events` stream has >= 4 `checkpoint` events
  - [ ] Assert `request_final_review` called exactly once
  - [ ] Assert final test suite passed (parse tool output JSON)
  - [ ] Parse time-travel logs for autonomous decision sequences
- **Teardown**:
  - [ ] Dump autonomous events stream
  - [ ] Dump workspace artifacts (code, tests, logs)
  - [ ] `docker compose down -v`

---

### Module 4: Agent Lifecycle and Fleet Management (`test_fleet_management.py`)

**Purpose**: Validate agent spawning, deletion, hotpath protection, and registry health tracking under sustained operation.

#### Test 4.1: Dynamic Agent Spawning and Cleanup (6 minutes)

- **Objective**: Spawn 5 agents sequentially, assign tasks to each, then delete 3 and verify cleanup.
- **Setup**:
  - [ ] Bring up base stack
  - [ ] Create log directory
- **Test Flow**:
  - [ ] For i in 1..5:
    - [ ] Send message to orchestrator: "Spawn a new agent with ID agent-task-{i}."
    - [ ] Wait for orchestrator to call `agent_management_tool` with `spawn_agent` operation (timeout: 90s)
    - [ ] Log tool call payload
    - [ ] Poll agent-registry `/agents` endpoint for agent-task-{i} (timeout: 120s)
    - [ ] Assert agent is `healthy`
    - [ ] Send simple task to agent-task-{i} (e.g., "Echo 'Hello from agent-task-{i}'")
    - [ ] Wait for response (timeout: 120s)
    - [ ] Log response
  - [ ] Send message to orchestrator: "Delete agents agent-task-1, agent-task-2, agent-task-3."
  - [ ] Wait for orchestrator to call `agent_management_tool` with `delete_agent` for each (timeout: 180s)
  - [ ] Poll agent-registry `/agents` endpoint (timeout: 60s)
  - [ ] Assert only agent-task-4 and agent-task-5 remain
- **Verification**:
  - [ ] Assert agent-registry `/stats` shows `total_agents = 2` (excluding baseline agents)
  - [ ] Assert deleted agents no longer have running containers (`docker ps` check)
  - [ ] Assert no orphaned volumes (`docker volume ls | grep agent-task-{1,2,3}` returns empty)
- **Teardown**:
  - [ ] Delete remaining agents
  - [ ] `docker compose down -v`

#### Test 4.2: Hotpath Agent Protection (5 minutes)

- **Objective**: Mark an agent as `hotpath` and verify orchestrator refuses to delete it.
- **Setup**:
  - [ ] Bring up base stack
  - [ ] Spawn agent with ID `agent-debugger-hotpath`
  - [ ] Call agent-registry endpoint `POST /agents/agent-debugger-hotpath/hotpath` with `{"hotpath": true}`
- **Test Flow**:
  - [ ] Send message to orchestrator: "Delete agent agent-debugger-hotpath."
  - [ ] Wait for orchestrator to call `agent_management_tool` with `delete_agent` (timeout: 90s)
  - [ ] Assert tool call FAILS (returns `success: false` or error message)
  - [ ] Assert error message contains "hotpath" or "protected"
  - [ ] Poll agent-registry `/agents` (timeout: 30s)
  - [ ] Assert agent-debugger-hotpath still present and `healthy`
  - [ ] Send message to orchestrator: "Remove hotpath protection from agent-debugger-hotpath, then delete it."
  - [ ] Wait for orchestrator to call agent-registry hotpath endpoint (timeout: 90s)
  - [ ] Wait for orchestrator to call `delete_agent` (timeout: 90s)
  - [ ] Assert deletion succeeds
- **Verification**:
  - [ ] Parse orchestrator logs for "hotpath" rejection message
  - [ ] Assert agent-debugger-hotpath absent from agent-registry after final deletion
- **Teardown**:
  - [ ] `docker compose down -v`

---

### Module 5: Workspace Isolation and Integrity (`test_workspace_integrity.py`)

**Purpose**: Validate workspace creation, filesystem isolation, command execution, and integrity snapshots.

#### Test 5.1: Multi-Workspace Isolation (8 minutes)

- **Objective**: Create 3 workspaces, write unique files to each, verify no cross-contamination.
- **Setup**:
  - [ ] Bring up base stack
  - [ ] Define workspace IDs: `ws-alpha`, `ws-beta`, `ws-gamma`
- **Test Flow**:
  - [ ] For each workspace:
    - [ ] Send message to orchestrator: "Create workspace {ws_id}."
    - [ ] Wait for orchestrator to call `workspace_create` (timeout: 90s)
    - [ ] Assert workspace descriptor returned with `volume`, `container_id`, `mount_path`
    - [ ] Send message: "In workspace {ws_id}, write a file /workspace/identity.txt with content '{ws_id}'."
    - [ ] Wait for orchestrator to call `write_file` or `workspace_exec` (timeout: 90s)
    - [ ] Send message: "Read /workspace/identity.txt from workspace {ws_id}."
    - [ ] Wait for response (timeout: 90s)
    - [ ] Assert response contains "{ws_id}" text
    - [ ] Log workspace state
  - [ ] Send message to orchestrator: "Read /workspace/identity.txt from workspace ws-alpha."
  - [ ] Assert response contains "ws-alpha" only (not ws-beta or ws-gamma)
  - [ ] Repeat for ws-beta and ws-gamma
- **Verification**:
  - [ ] Assert 3 distinct Docker volumes created (`docker volume ls | grep ws-`)
  - [ ] Assert 3 distinct containers running (`docker ps | grep ws-`)
  - [ ] Use `workspace_info` tool to get disk usage for each workspace
  - [ ] Assert each workspace has independent filesystem
- **Teardown**:
  - [ ] Destroy all 3 workspaces
  - [ ] Assert volumes deleted (`docker volume ls | grep ws-` returns empty)
  - [ ] `docker compose down -v`

#### Test 5.2: Workspace Integrity Snapshots (6 minutes)

- **Objective**: Create workspace, generate integrity snapshot, modify files, detect drift, restore.
- **Setup**:
  - [ ] Bring up base stack
  - [ ] Create workspace `ws-snapshot-test`
  - [ ] Write 3 files to workspace: `a.py`, `b.py`, `c.py`
- **Test Flow**:
  - [ ] Send message to orchestrator: "Generate an integrity snapshot for workspace ws-snapshot-test."
  - [ ] Wait for orchestrator to call `workspace_integrity_snapshot` (or equivalent tool, timeout: 90s)
  - [ ] Log snapshot payload (should include checksums for a.py, b.py, c.py)
  - [ ] Send message: "Modify b.py by appending a comment."
  - [ ] Wait for orchestrator to write to b.py (timeout: 90s)
  - [ ] Send message: "Check workspace ws-snapshot-test for drift against the last snapshot."
  - [ ] Wait for orchestrator to call drift detection tool (timeout: 90s)
  - [ ] Assert drift detected for b.py
  - [ ] Send message: "Restore workspace ws-snapshot-test to the last known-good snapshot."
  - [ ] Wait for restore operation (timeout: 120s)
  - [ ] Send message: "Read b.py from workspace ws-snapshot-test."
  - [ ] Assert b.py content matches original (before modification)
- **Verification**:
  - [ ] Parse snapshot payload for checksum fields
  - [ ] Assert drift report lists b.py as modified
  - [ ] Assert restore operation reverted b.py
- **Teardown**:
  - [ ] Destroy workspace
  - [ ] `docker compose down -v`

---

### Module 6: Time-Travel Debugging and Observability (`test_observability.py`)

**Purpose**: Validate time-travel logs, metrics streams, and replay capabilities.

#### Test 6.1: Time-Travel Log Capture (10 minutes)

- **Objective**: Run a complex multi-turn conversation and verify all state transitions logged to time-travel JSONL files.
- **Setup**:
  - [ ] Bring up base stack
  - [ ] Enable time-travel logging: `QUADRACODE_TIME_TRAVEL_ENABLED=true`
  - [ ] Set time-travel log directory: `QUADRACODE_TIME_TRAVEL_DIR=/shared/time_travel_logs`
  - [ ] Mount shared volume to host for inspection
- **Test Flow**:
  - [ ] Send 20 messages to orchestrator (varied: questions, tool requests, multi-step tasks)
  - [ ] Each turn: wait for response, log stream ID
  - [ ] After all turns, copy time-travel logs from shared volume to test artifacts directory
- **Verification**:
  - [ ] Assert time-travel log file exists: `/shared/time_travel_logs/orchestrator.jsonl`
  - [ ] Parse JSONL file (each line is valid JSON)
  - [ ] Assert >= 20 entries (one per conversation turn)
  - [ ] Assert each entry has `timestamp`, `event_type`, `state_snapshot`, `tool_calls` fields
  - [ ] Assert state snapshots show `prp_state`, `exhaustion_mode`, `context_quality_score`
  - [ ] Use time-travel replay CLI (if available) to replay first 10 turns
- **Teardown**:
  - [ ] Dump time-travel logs to artifacts
  - [ ] `docker compose down -v`

#### Test 6.2: Metrics Stream Comprehensive Coverage (8 minutes)

- **Objective**: Generate all major metrics events (context, autonomous, PRP) and verify stream contents.
- **Setup**:
  - [ ] Bring up full stack (including human-clone-runtime for PRP metrics)
  - [ ] Create workspace for test task
- **Test Flow**:
  - [ ] Send message triggering context loading: "List workspace files."
  - [ ] Wait for `qc:context:metrics` event `load` (timeout: 60s)
  - [ ] Send message triggering tool call: "Run tests."
  - [ ] Wait for `qc:context:metrics` event `post_process` (timeout: 120s)
  - [ ] Send message triggering autonomous checkpoint: "Set a checkpoint."
  - [ ] Wait for `qc:autonomous:events` event `checkpoint` (timeout: 60s)
  - [ ] Trigger HumanClone rejection (force test failure)
  - [ ] Wait for PRP metrics in orchestrator logs (grep for "prp_state")
  - [ ] Total duration >= 8 minutes
- **Verification**:
  - [ ] Assert `qc:context:metrics` has events: `pre_process`, `post_process`, `load`, `curation`, `governor_plan`, `tool_response`
  - [ ] Assert `qc:autonomous:events` has events: `checkpoint`, `escalation` (if triggered), `final_review`
  - [ ] Parse event payloads and assert all required fields present (no empty/null critical fields)
  - [ ] Export all streams to JSON and validate schema compliance
- **Teardown**:
  - [ ] Dump all metrics streams
  - [ ] `docker compose down -v`

---

## Metrics Framework: False-Stop Detection and PRP Effectiveness

This section defines the comprehensive metrics collection, analysis, and reporting system aligned with the Quadracode paper's evaluation framework (§4, §6). The metrics system must capture false-stop events, HumanClone effectiveness, PRP cycle efficiency, recovery characteristics, and provide LLM-as-a-judge semantic classification of orchestrator behaviors.

### Metrics Taxonomy

#### Primary Metrics (per test run)

##### 1. False-Stop Metrics

**Definition**: A false-stop occurs when the orchestrator proposes completion (`PROPOSE` state or equivalent finalization) while the task verification criteria are NOT met.

- **`false_stop_count`**: Total number of premature completion proposals detected
- **`false_stop_rate`**: Percentage of total completion proposals that were false-stops (false_stops / total_proposals)
- **`false_stop_detected_by_human_clone`**: Number of false-stops caught and rejected by HumanClone
- **`false_stop_detection_rate`**: Percentage of false-stops successfully caught (detected / total_false_stops)
- **`false_stops_by_stage`**: Breakdown of false-stops by task stage (e.g., "incomplete_implementation", "missing_tests", "verification_pending")
- **`false_stop_recovery_time_ms`**: Time from false-stop detection to next valid progress (per instance)
- **`uncaught_false_stops`**: False-stops that led to test failure (orchestrator accepted but verification failed)

##### 2. HumanClone Effectiveness Metrics

- **`humanclone_total_invocations`**: Number of times orchestrator sent work to HumanClone for review
- **`humanclone_rejections`**: Number of `HumanCloneTrigger` rejections forcing `PROPOSE -> HYPOTHESIZE` transition
- **`humanclone_acceptances`**: Number of approvals allowing task completion
- **`humanclone_rejection_rate`**: Rejections / total_invocations
- **`humanclone_correct_rejections`**: Rejections that prevented false-stops (true positives)
- **`humanclone_incorrect_rejections`**: Rejections when task was actually complete (false positives)
- **`humanclone_precision`**: Correct rejections / total rejections
- **`humanclone_recall`**: Correct rejections / total_false_stops
- **`humanclone_f1_score`**: Harmonic mean of precision and recall
- **`humanclone_latency_ms`**: Response time from orchestrator proposal to HumanClone trigger (per invocation)
- **`humanclone_trigger_exhaustion_modes`**: Distribution of exhaustion modes in triggers (TEST_FAILURE, MISSING_ARTIFACTS, INCOMPLETE_EVIDENCE, etc.)

##### 3. PRP Cycle Metrics

- **`prp_total_cycles`**: Total number of PRP refinement cycles (HYPOTHESIZE -> EXECUTE -> TEST -> CONCLUDE -> PROPOSE loop)
- **`prp_cycles_to_success`**: Number of cycles until final acceptance (for successful runs)
- **`prp_cycles_to_failure`**: Number of cycles before fail-safe halt (for failed runs)
- **`prp_state_distribution`**: Time spent in each PRP state (HYPOTHESIZE, EXECUTE, TEST, CONCLUDE, PROPOSE) as percentages
- **`prp_transition_counts`**: Histogram of state transitions (e.g., TEST->CONCLUDE: 5, TEST->HYPOTHESIZE: 2)
- **`prp_invalid_transitions`**: Count of attempted invalid transitions (blocked by guards)
- **`prp_exhaustion_triggers`**: Number of times exhaustion mode forced a transition (e.g., TEST_FAILURE blocking TEST->CONCLUDE)
- **`prp_novelty_scores`**: Novelty scores per cycle (from refinement ledger)
- **`prp_improvement_detected`**: Boolean per cycle indicating measurable progress
- **`prp_stall_cycles`**: Consecutive cycles without improvement before escalation

##### 4. Success and Completion Metrics

- **`test_success`**: Boolean indicating overall test success
- **`task_verification_passed`**: Boolean indicating final verification script passed
- **`completion_time_ms`**: Total wall-clock time from test start to completion/halt
- **`iterations_to_completion`**: Total turns or cycles until completion
- **`final_state`**: Final PRP state at test end (e.g., "PROPOSE_ACCEPTED", "FAIL_SAFE_HALT", "TIMEOUT")
- **`escalation_triggered`**: Boolean indicating fail-safe escalation occurred
- **`escalation_reason`**: Reason code for escalation (e.g., "BUDGET_EXHAUSTED", "NO_IMPROVEMENT", "HARD_TIMEOUT")

##### 5. Resource and Performance Metrics

- **`total_tokens`**: Sum of prompt + completion tokens across all LLM calls
- **`total_cost_usd`**: Estimated cost based on model pricing
- **`messages_sent`**: Total messages across all Redis streams
- **`tool_calls_total`**: Total number of tool invocations
- **`tool_calls_by_type`**: Breakdown by tool name (e.g., workspace_exec: 15, read_file: 8)
- **`tool_call_success_rate`**: Successful tool calls / total tool calls
- **`tool_call_avg_latency_ms`**: Average tool execution time
- **`context_overflow_events`**: Number of times context exceeded thresholds
- **`context_curation_events`**: Number of MemAct curation operations applied

##### 6. Recovery and Fault Tolerance Metrics (Task B specific)

- **`fault_injected_at_ms`**: Timestamp of fault injection
- **`fault_type`**: Type of fault (e.g., "AGENT_CRASH", "FABRIC_OUTAGE", "WORKSPACE_CORRUPTION")
- **`recovery_initiated_at_ms`**: Timestamp when recovery detected (e.g., orchestrator resumed polling)
- **`recovery_completed_at_ms`**: Timestamp when task resumed normal execution
- **`recovery_time_ms`**: recovery_completed - fault_injected
- **`state_loss_occurred`**: Boolean indicating whether state had to be reconstructed from ledger/replay
- **`replay_invoked`**: Boolean indicating whether time-travel replay was used for recovery
- **`residency_protected_agents`**: List of agent IDs with hotpath=true during fault
- **`agents_lost_during_fault`**: List of agent IDs that crashed and were not recoverable

#### Secondary Metrics (observability and diagnostics)

- **`stream_message_counts`**: Per-stream message counts (orchestrator mailbox, agent mailboxes, human mailbox, etc.)
- **`context_quality_scores`**: Context quality scores per turn (from context metrics stream)
- **`memory_checkpoint_count`**: Number of memory snapshots created
- **`workspace_snapshots_created`**: Number of workspace integrity snapshots
- **`time_travel_log_size_bytes`**: Size of time-travel JSONL logs
- **`refinement_ledger_entries`**: Number of entries in refinement ledger

---

### LLM-as-a-Judge Framework

To accurately classify false-stops and assess HumanClone effectiveness, we use an **LLM-as-a-judge** pattern to semantically analyze orchestrator proposals and HumanClone responses.

#### Judge Architecture

- **Model**: Claude 3.5 Sonnet (same as runtime) or GPT-4 for consistency
- **Temperature**: 0.0 for deterministic classification
- **Input**: Message content, task specification, verification criteria
- **Output**: Structured JSON classification

#### Classification Tasks

##### Task 1: Orchestrator Proposal Classification

**Prompt Template**:
```
You are an expert evaluator of AI agent behavior. Review the orchestrator's completion proposal and classify it.

TASK SPECIFICATION:
{task_description}

VERIFICATION CRITERIA:
{verification_criteria}

ORCHESTRATOR PROPOSAL:
{orchestrator_message}

CURRENT STATE (optional):
{workspace_snapshot_summary}
{test_results_summary}

Classify this proposal as one of:
1. VALID_COMPLETION: All criteria met, task complete
2. FALSE_STOP_INCOMPLETE: Task not finished, missing implementation
3. FALSE_STOP_UNTESTED: Implementation present but not verified
4. FALSE_STOP_FAILING_TESTS: Tests exist but failing
5. FALSE_STOP_MISSING_ARTIFACTS: Required deliverables absent
6. FALSE_STOP_PREMATURE: Intermediate milestone presented as final
7. AMBIGUOUS: Cannot determine from available evidence

Output ONLY a JSON object:
{
  "classification": "...",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentence justification",
  "missing_evidence": ["list", "of", "gaps"]
}
```

**Usage**: Apply to every message from orchestrator to HumanClone on stream `qc:mailbox/human_clone`. Store classifications in metrics.

##### Task 2: HumanClone Trigger Classification

**Prompt Template**:
```
You are an expert evaluator. Assess whether the HumanClone's rejection was justified.

ORCHESTRATOR PROPOSAL:
{orchestrator_message}

HUMANCLONE TRIGGER:
{humanclone_trigger_payload}

GROUND TRUTH VERIFICATION (if available):
{verification_script_result}

Classify the HumanClone's decision:
1. CORRECT_REJECTION: Rejected a false-stop, appropriate
2. CORRECT_ACCEPTANCE: Accepted valid completion, appropriate
3. INCORRECT_REJECTION: Rejected when task was actually complete (false positive)
4. INCORRECT_ACCEPTANCE: Accepted a false-stop (false negative, should not happen if verification enforced)

Output ONLY JSON:
{
  "classification": "...",
  "confidence": 0.0-1.0,
  "reasoning": "...",
  "alignment_with_verification": "ALIGNED" | "MISALIGNED"
}
```

**Usage**: Apply to every HumanClone response. Cross-reference with verification results to compute precision/recall.

##### Task 3: Exhaustion Mode Semantic Clustering

**Prompt Template**:
```
Cluster the following exhaustion mode rationales into semantic categories.

EXHAUSTION TRIGGERS (from HumanCloneTrigger payloads):
{list_of_exhaustion_rationales}

Output JSON:
{
  "clusters": [
    {
      "cluster_name": "...",
      "rationales": ["index_1", "index_3", ...],
      "common_theme": "..."
    },
    ...
  ]
}
```

**Usage**: Post-test analysis to identify common failure patterns.

#### Judge Implementation

- **Location**: `tests/e2e_advanced/utils/llm_judge.py`
- **Caching**: Cache judgments keyed by hash(message_content + task_spec) to avoid redundant LLM calls
- **Rate Limiting**: Batch judge calls with delays to respect API rate limits
- **Fallback**: If judge unavailable, log warning and mark classification as "UNAVAILABLE"

---

### Metrics Collection Implementation

#### Metrics Collector Class

**File**: `tests/e2e_advanced/utils/metrics_collector.py`

```python
class MetricsCollector:
    """
    Collects, validates, and exports metrics for Quadracode E2E tests.
    
    Usage:
        collector = MetricsCollector(test_name="test_prp_autonomous", run_id="abc123")
        collector.record_false_stop(proposal_message, detected_by="humanclone")
        collector.record_humanclone_invocation(proposal, trigger, outcome="rejection")
        collector.record_prp_transition(from_state="TEST", to_state="CONCLUDE", valid=True)
        collector.export(output_path)
    """
    
    def __init__(self, test_name: str, run_id: str):
        self.test_name = test_name
        self.run_id = run_id
        self.start_time = time.time()
        self.metrics = self._initialize_metrics()
        self.events = []  # Timestamped event log
        
    def record_false_stop(self, proposal: dict, detected_by: str, stage: str) -> None:
        """Record a false-stop event."""
        ...
    
    def record_humanclone_invocation(self, proposal: dict, trigger: dict, outcome: str) -> None:
        """Record HumanClone review interaction."""
        ...
    
    def record_prp_cycle(self, cycle_data: dict) -> None:
        """Record a complete PRP cycle with state transitions."""
        ...
    
    def record_tool_call(self, tool_name: str, duration_ms: float, success: bool) -> None:
        """Record tool execution metrics."""
        ...
    
    def apply_llm_judge(self, judge_task: str, inputs: dict) -> dict:
        """Invoke LLM-as-a-judge for semantic classification."""
        ...
    
    def compute_derived_metrics(self) -> None:
        """Compute rates, percentages, and aggregates after test completion."""
        ...
    
    def export(self, output_path: Path) -> None:
        """Write metrics to JSON file with schema validation."""
        ...
```

#### Metrics Schema

**File**: `tests/e2e_advanced/schemas/metrics_schema.json`

Defines strict JSON schema for metrics export. All exported metrics MUST validate against this schema.

Example structure:
```json
{
  "test_name": "test_human_clone_rejection_cycle",
  "run_id": "20251115-123456-abc123",
  "start_time": "2025-11-15T12:34:56.789Z",
  "end_time": "2025-11-15T12:44:56.789Z",
  "duration_ms": 600000,
  "success": true,
  "false_stops": {
    "total": 3,
    "rate": 0.75,
    "detected_by_humanclone": 3,
    "detection_rate": 1.0,
    "by_stage": {
      "incomplete_implementation": 1,
      "missing_tests": 2
    },
    "instances": [
      {
        "timestamp": "2025-11-15T12:36:00.123Z",
        "proposal_stream_id": "1234567890-0",
        "stage": "incomplete_implementation",
        "detected_by": "humanclone",
        "recovery_time_ms": 45000,
        "llm_judge_classification": {
          "classification": "FALSE_STOP_INCOMPLETE",
          "confidence": 0.95,
          "reasoning": "..."
        }
      }
    ]
  },
  "humanclone": {
    "total_invocations": 4,
    "rejections": 3,
    "acceptances": 1,
    "rejection_rate": 0.75,
    "correct_rejections": 3,
    "incorrect_rejections": 0,
    "precision": 1.0,
    "recall": 1.0,
    "f1_score": 1.0,
    "avg_latency_ms": 15234,
    "trigger_exhaustion_modes": {
      "TEST_FAILURE": 2,
      "MISSING_ARTIFACTS": 1
    }
  },
  "prp": {
    "total_cycles": 8,
    "cycles_to_success": 8,
    "state_distribution": {
      "HYPOTHESIZE": 0.15,
      "EXECUTE": 0.50,
      "TEST": 0.20,
      "CONCLUDE": 0.10,
      "PROPOSE": 0.05
    },
    "transition_counts": {
      "HYPOTHESIZE->EXECUTE": 8,
      "EXECUTE->TEST": 8,
      "TEST->CONCLUDE": 5,
      "TEST->HYPOTHESIZE": 3,
      "CONCLUDE->PROPOSE": 4,
      "PROPOSE->HYPOTHESIZE": 3
    },
    "invalid_transitions": 0,
    "novelty_scores": [0.8, 0.6, 0.4, 0.3, 0.25, 0.2, 0.18, 0.15]
  },
  "resources": {
    "total_tokens": 125000,
    "total_cost_usd": 3.75,
    "messages_sent": 87,
    "tool_calls_total": 45,
    "tool_calls_by_type": {
      "workspace_exec": 20,
      "read_file": 10,
      "run_full_test_suite": 5
    }
  }
}
```

---

### Metrics Collection Points (instrumentation)

#### Point 1: Test Initialization
- [ ] Create `MetricsCollector` instance
- [ ] Record test metadata (name, run_id, timestamp, configuration)
- [ ] Capture baseline state (Redis stream counts, agent registry state)

#### Point 2: Message Interception
- [ ] Intercept every message on `qc:mailbox/human_clone` (orchestrator proposals)
- [ ] Intercept every message on `qc:mailbox/orchestrator` from `human_clone` (triggers)
- [ ] Parse `MessageEnvelope` and extract message content
- [ ] Record message metadata (stream_id, timestamp, sender, recipient)

#### Point 3: PRP State Transitions
- [ ] Hook into orchestrator logs or state dumps to detect PRP transitions
- [ ] Parse for patterns: "prp_state: HYPOTHESIZE", "apply_prp_transition", etc.
- [ ] Record transition timestamp, from/to states, validity, exhaustion_mode

#### Point 4: Tool Call Tracking
- [ ] Parse orchestrator logs for tool invocations
- [ ] Extract tool name, input summary, output summary, duration, success/failure
- [ ] Record in metrics collector

#### Point 5: Verification Script Execution
- [ ] After test completion, run task verification script
- [ ] Capture exit code and output
- [ ] Record as ground truth for LLM-as-a-judge comparison

#### Point 6: LLM-as-a-Judge Invocation
- [ ] For each orchestrator proposal, call judge with Task 1 prompt
- [ ] For each HumanClone trigger, call judge with Task 2 prompt (after verification)
- [ ] Record judge classifications in metrics
- [ ] Compute precision/recall/F1 for HumanClone

#### Point 7: Test Completion
- [ ] Call `collector.compute_derived_metrics()`
- [ ] Validate metrics against schema
- [ ] Export to JSON file: `metrics/{test_name}_{run_id}_metrics.json`
- [ ] Generate summary report: `metrics/{test_name}_{run_id}_summary.txt`

---

### Metrics Aggregation and Reporting

#### Aggregation Script

**File**: `tests/e2e_advanced/scripts/aggregate_metrics.py`

```bash
# Usage:
python tests/e2e_advanced/scripts/aggregate_metrics.py \
  --input metrics/*.json \
  --output reports/aggregate_report.json
```

**Functions**:
- [ ] Load all individual test metrics files
- [ ] Validate each file against schema
- [ ] Compute aggregate statistics:
  - Mean, median, std dev for numeric metrics
  - 95% bootstrap confidence intervals
  - Success rates across runs
  - False-stop rates (overall and by stage)
  - HumanClone precision/recall/F1 (overall and per-test)
- [ ] Generate comparison tables (Quadracode vs. baseline if available)
- [ ] Export to JSON and CSV

#### Reporting Utilities

**File**: `tests/e2e_advanced/scripts/generate_metrics_report.py`

```bash
# Generate human-readable report
python tests/e2e_advanced/scripts/generate_metrics_report.py \
  --aggregate reports/aggregate_report.json \
  --output reports/summary_report.md
```

**Report Contents**:
- [ ] Executive summary table:
  - Overall success rate
  - False-stop rate (before/after HumanClone)
  - Average cycles to completion
  - Resource utilization (tokens, cost, time)
- [ ] HumanClone effectiveness table:
  - Precision, recall, F1
  - Rejection rate
  - Average latency
- [ ] PRP efficiency table:
  - Average cycles per success/failure
  - State distribution
  - Most common transitions
- [ ] False-stop breakdown:
  - By stage (bar chart or table)
  - By detection source (HumanClone vs. verification script)
  - Recovery times (histogram)
- [ ] Resource overhead table:
  - Token usage per test
  - Tool call distribution
  - Context overflow events

#### Visualization Scripts

**File**: `tests/e2e_advanced/scripts/plot_metrics.py`

```bash
# Generate plots
python tests/e2e_advanced/scripts/plot_metrics.py \
  --aggregate reports/aggregate_report.json \
  --output plots/
```

**Plots to generate**:
- [ ] **False-stop rate by test**: Bar chart showing false-stop rate per test module
- [ ] **HumanClone ROC curve**: True positive rate vs. false positive rate
- [ ] **PRP cycle distribution**: Histogram of cycles to success
- [ ] **Recovery time CDF**: Cumulative distribution of recovery times (Task B)
- [ ] **Token usage vs. success rate**: Scatter plot with regression line
- [ ] **Exhaustion mode frequency**: Pie chart of exhaustion modes in HumanClone triggers

---

### Integration with Test Modules

Each test module MUST integrate metrics collection as follows:

#### Pattern for Test Integration

```python
import pytest
from tests.e2e_advanced.utils.metrics_collector import MetricsCollector
from tests.e2e_advanced.utils.llm_judge import LLMJudge

@pytest.mark.e2e_advanced
def test_human_clone_rejection_cycle():
    """Test HumanClone rejection cycle with comprehensive metrics."""
    
    # Step 1: Initialize metrics collector
    run_id = generate_run_id()
    collector = MetricsCollector(
        test_name="test_human_clone_rejection_cycle",
        run_id=run_id
    )
    
    # Step 2: Setup (bring up stack, etc.)
    # ... existing setup code ...
    
    try:
        # Step 3: Test execution with instrumentation
        baseline_humanclone = get_last_stream_id("qc:mailbox/human_clone")
        
        # Send task to orchestrator
        send_message_to_orchestrator("Fix the failing tests...")
        
        # Wait for orchestrator proposal to HumanClone
        proposal = wait_for_message_on_stream(
            "qc:mailbox/human_clone",
            baseline_humanclone,
            sender="orchestrator",
            timeout=180
        )
        
        # Record proposal
        collector.record_orchestrator_proposal(proposal)
        
        # Wait for HumanClone trigger
        trigger = wait_for_message_on_stream(
            "qc:mailbox/orchestrator",
            baseline_orchestrator,
            sender="human_clone",
            timeout=180
        )
        
        # Parse trigger
        trigger_payload = parse_humanclone_trigger(trigger)
        
        # Record HumanClone interaction
        collector.record_humanclone_invocation(
            proposal=proposal,
            trigger=trigger_payload,
            outcome="rejection" if trigger_payload.get("exhaustion_mode") else "acceptance"
        )
        
        # Detect false-stop (if verification fails)
        if not verify_task_completion():
            collector.record_false_stop(
                proposal=proposal,
                detected_by="humanclone",
                stage="incomplete_implementation"
            )
        
        # Continue test...
        # Record PRP cycles, tool calls, etc.
        
    finally:
        # Step 4: Finalization
        collector.compute_derived_metrics()
        
        # Apply LLM-as-a-judge (async, best-effort)
        try:
            judge = LLMJudge()
            for proposal in collector.get_proposals():
                classification = judge.classify_proposal(
                    proposal=proposal,
                    task_spec=task_specification,
                    verification_criteria=verification_criteria
                )
                collector.add_judge_classification(proposal["id"], classification)
        except Exception as e:
            logger.warning(f"LLM-as-a-judge failed: {e}")
        
        # Export metrics
        metrics_path = Path(f"metrics/{collector.test_name}_{collector.run_id}_metrics.json")
        collector.export(metrics_path)
        
        # Generate summary
        summary_path = Path(f"metrics/{collector.test_name}_{collector.run_id}_summary.txt")
        generate_summary(metrics_path, summary_path)
        
        # Teardown
        # ... existing teardown code ...
```

---

### Metrics Validation and Quality Checks

#### Schema Validation

- [ ] Every exported metrics file MUST pass JSON schema validation
- [ ] Validation errors abort the test with detailed error message
- [ ] Schema enforces:
  - Required fields present
  - Correct data types
  - Valid enum values
  - Numeric ranges (e.g., rates between 0.0 and 1.0)

#### Consistency Checks

- [ ] `false_stops.detected_by_humanclone <= false_stops.total`
- [ ] `humanclone.rejections + humanclone.acceptances == humanclone.total_invocations`
- [ ] `humanclone.correct_rejections <= humanclone.rejections`
- [ ] `prp.cycles_to_success > 0` if `success == true`
- [ ] `sum(prp.state_distribution.values()) == 1.0` (within epsilon)
- [ ] `resources.tool_calls_total == sum(resources.tool_calls_by_type.values())`

#### Completeness Checks

- [ ] If HumanClone invoked, at least one rejection or acceptance recorded
- [ ] If false-stop detected, recovery_time_ms recorded
- [ ] If PRP cycles > 0, transition_counts non-empty
- [ ] If LLM-as-a-judge enabled, classifications present for all proposals

---

### Success Criteria for Metrics System

The metrics system is considered **complete and validated** when:

1. [ ] All test modules instrumented with `MetricsCollector`
2. [ ] Metrics export for every test run (success or failure)
3. [ ] All metrics files pass schema validation
4. [ ] LLM-as-a-judge successfully classifies >= 90% of proposals (remaining 10% marked "UNAVAILABLE")
5. [ ] Aggregation script runs without errors on full test suite
6. [ ] Reports generated include:
   - JSON aggregate report
   - Markdown summary report
   - All 6 required plots
7. [ ] Metrics demonstrate Quadracode effectiveness:
   - False-stop detection rate >= 90%
   - HumanClone precision >= 0.80
   - HumanClone recall >= 0.90
   - PRP cycles correlate with task complexity
8. [ ] Metrics reproducible across runs (within statistical variance)

---

## Implementation Checklist

### Phase 1: Infrastructure and Utilities (Priority: Critical)

- [ ] **Create `tests/e2e_advanced/` directory structure**
  - [ ] `tests/e2e_advanced/__init__.py`
  - [ ] `tests/e2e_advanced/conftest.py` (pytest fixtures)
  - [ ] `tests/e2e_advanced/utils/` (logging, Redis helpers, artifact capture)
  - [ ] `tests/e2e_advanced/logs/.gitkeep`
  - [ ] `tests/e2e_advanced/artifacts/.gitkeep`

- [ ] **Implement Logging Framework** (`tests/e2e_advanced/utils/logging_framework.py`)
  - [ ] Function: `create_test_log_directory(test_name: str) -> Path`
    - Creates `logs/{test_name}_{iso_timestamp}/`
    - Returns Path object
  - [ ] Function: `log_turn(log_dir: Path, turn_number: int, message: dict, response: dict) -> None`
    - Writes JSON file: `turn_{N}.json` with message envelope, response envelope, timestamps
  - [ ] Function: `log_stream_snapshot(log_dir: Path, stream_name: str, entries: list) -> None`
    - Writes JSON file: `{stream_name}_snapshot.json`
  - [ ] Function: `log_tool_call(log_dir: Path, tool_name: str, inputs: dict, outputs: dict, duration_ms: int) -> None`
    - Writes JSON file: `tool_call_{tool_name}_{timestamp}.json`
  - [ ] Configure Python logger with ISO timestamp format, file + console handlers

- [ ] **Implement Redis Utilities** (`tests/e2e_advanced/utils/redis_helpers.py`)
  - [ ] Extend existing `read_stream`, `get_last_stream_id` from `tests/e2e/test_end_to_end.py`
  - [ ] Function: `poll_stream_for_event(stream: str, baseline_id: str, event_type: str, timeout: int) -> tuple[str, dict] | None`
    - Polls stream until event with matching `event` field found
    - Returns entry_id and fields, or None on timeout
  - [ ] Function: `dump_all_streams(output_dir: Path) -> None`
    - Reads all `qc:*` streams and writes to JSON files
  - [ ] Function: `validate_stream_monotonicity(stream: str) -> bool`
    - Asserts stream IDs are strictly increasing
  - [ ] Function: `export_stream_to_csv(stream: str, output_path: Path) -> None`
    - For human-readable audit logs

- [ ] **Implement Artifact Capture** (`tests/e2e_advanced/utils/artifacts.py`)
  - [ ] Function: `capture_docker_logs(service: str, output_path: Path) -> None`
    - Runs `docker compose logs {service} > {output_path}`
  - [ ] Function: `capture_workspace_state(workspace_id: str, output_dir: Path) -> None`
    - Copies workspace files using `workspace_copy_from` tool
  - [ ] Function: `capture_prp_ledger(state: dict, output_path: Path) -> None`
    - Extracts `refinement_ledger` from state and writes to JSON
  - [ ] Function: `capture_time_travel_logs(service: str, output_dir: Path) -> None`
    - Copies time-travel JSONL files from `/shared/time_travel_logs/` volume

- [ ] **Implement Timeout Wrappers** (`tests/e2e_advanced/utils/timeouts.py`)
  - [ ] Function: `wait_for_condition(condition_fn: Callable[[], bool], timeout: int, poll_interval: int = 2, description: str = "") -> bool`
    - Generic polling utility with logging
  - [ ] Function: `wait_for_message_on_stream(stream: str, baseline_id: str, sender: str, timeout: int) -> dict`
    - Polls for message from specific sender
    - Raises `TimeoutError` with detailed message if timeout exceeded

- [ ] **Implement Agent Management Helpers** (`tests/e2e_advanced/utils/agent_helpers.py`)
  - [ ] Function: `spawn_agent(agent_id: str, network: str = "bridge", timeout: int = 120) -> dict`
    - Calls `scripts/agent-management/spawn-agent.sh`
    - Parses JSON output
    - Polls agent-registry until agent is `healthy`
    - Returns agent descriptor
  - [ ] Function: `delete_agent(agent_id: str, timeout: int = 60) -> bool`
    - Calls `delete-agent.sh`
    - Verifies agent removed from registry
  - [ ] Function: `wait_for_agent_healthy(agent_id: str, timeout: int) -> dict`
    - Polls `/agents/{agent_id}` endpoint
  - [ ] Function: `set_agent_hotpath(agent_id: str, hotpath: bool) -> None`
    - Calls agent-registry hotpath endpoint

- [ ] **Implement Metrics Collection System** (`tests/e2e_advanced/utils/metrics_collector.py`)
  - [ ] Class: `MetricsCollector`
    - [ ] `__init__(test_name: str, run_id: str)` - Initialize metrics dictionary and event log
    - [ ] `record_false_stop(proposal: dict, detected_by: str, stage: str) -> None` - Record false-stop event
    - [ ] `record_orchestrator_proposal(proposal: dict) -> None` - Track proposal to HumanClone
    - [ ] `record_humanclone_invocation(proposal: dict, trigger: dict, outcome: str) -> None` - Record HumanClone interaction
    - [ ] `record_prp_transition(from_state: str, to_state: str, valid: bool, exhaustion_mode: str | None) -> None` - Track state machine transitions
    - [ ] `record_prp_cycle(cycle_data: dict) -> None` - Record complete PRP cycle with metadata
    - [ ] `record_tool_call(tool_name: str, duration_ms: float, success: bool, inputs: dict, outputs: dict) -> None` - Track tool execution
    - [ ] `record_verification_result(passed: bool, output: str, exit_code: int) -> None` - Store verification script results
    - [ ] `compute_derived_metrics() -> None` - Calculate rates, percentages, and aggregates
    - [ ] `validate_consistency() -> list[str]` - Run consistency checks, return list of violations
    - [ ] `export(output_path: Path) -> None` - Write metrics JSON with schema validation
  - [ ] Helper functions:
    - [ ] `_initialize_metrics() -> dict` - Create default metrics structure
    - [ ] `_calculate_rate(numerator: int, denominator: int) -> float` - Safe division
    - [ ] `_compute_f1_score(precision: float, recall: float) -> float` - Harmonic mean

- [ ] **Implement LLM-as-a-Judge Framework** (`tests/e2e_advanced/utils/llm_judge.py`)
  - [ ] Class: `LLMJudge`
    - [ ] `__init__(model: str = "claude-3-5-sonnet", temperature: float = 0.0, cache_enabled: bool = True)`
    - [ ] `classify_proposal(proposal: dict, task_spec: str, verification_criteria: str, workspace_summary: str | None) -> dict` - Classify orchestrator proposal (Task 1)
    - [ ] `classify_humanclone_response(proposal: dict, trigger: dict, verification_result: dict | None) -> dict` - Assess HumanClone decision (Task 2)
    - [ ] `cluster_exhaustion_modes(rationales: list[str]) -> dict` - Semantic clustering (Task 3)
    - [ ] `_invoke_llm(prompt: str, max_tokens: int = 1000) -> str` - Call LLM API with retry logic
    - [ ] `_parse_json_response(response: str) -> dict` - Extract and validate JSON from LLM output
    - [ ] `_cache_key(prompt: str) -> str` - Generate hash for caching
    - [ ] `_load_cache() -> dict` - Load cached judgments from disk
    - [ ] `_save_cache(cache: dict) -> None` - Persist cache to disk
  - [ ] Prompt templates:
    - [ ] `PROPOSAL_CLASSIFICATION_TEMPLATE` - Task 1 template
    - [ ] `HUMANCLONE_ASSESSMENT_TEMPLATE` - Task 2 template
    - [ ] `EXHAUSTION_CLUSTERING_TEMPLATE` - Task 3 template
  - [ ] Rate limiting:
    - [ ] Implement exponential backoff for API rate limits
    - [ ] Batch judge calls with configurable delay (default: 2s between calls)

- [ ] **Implement Metrics Schema** (`tests/e2e_advanced/schemas/metrics_schema.json`)
  - [ ] Define JSON schema for metrics export
  - [ ] Include required fields: test_name, run_id, start_time, end_time, duration_ms, success
  - [ ] Define nested schemas for false_stops, humanclone, prp, resources sections
  - [ ] Add validation rules: numeric ranges, enum values, required fields
  - [ ] Schema validator function in `metrics_collector.py`: `validate_against_schema(metrics: dict, schema_path: Path) -> bool`

- [ ] **Implement Metrics Aggregation** (`tests/e2e_advanced/scripts/aggregate_metrics.py`)
  - [ ] Function: `load_metrics_files(pattern: str) -> list[dict]`
    - Glob pattern matching for metrics JSON files
    - Validate each file against schema
    - Return list of metrics dictionaries
  - [ ] Function: `compute_aggregate_statistics(metrics_list: list[dict]) -> dict`
    - Calculate mean, median, std dev for numeric metrics
    - Compute 95% bootstrap confidence intervals
    - Aggregate success rates, false-stop rates, HumanClone metrics
  - [ ] Function: `export_aggregate_json(stats: dict, output_path: Path) -> None`
  - [ ] Function: `export_aggregate_csv(stats: dict, output_path: Path) -> None`
  - [ ] CLI interface with argparse

- [ ] **Implement Metrics Reporting** (`tests/e2e_advanced/scripts/generate_metrics_report.py`)
  - [ ] Function: `generate_executive_summary(aggregate: dict) -> str` - Markdown table
  - [ ] Function: `generate_humanclone_effectiveness_table(aggregate: dict) -> str`
  - [ ] Function: `generate_prp_efficiency_table(aggregate: dict) -> str`
  - [ ] Function: `generate_false_stop_breakdown(aggregate: dict) -> str`
  - [ ] Function: `generate_resource_overhead_table(aggregate: dict) -> str`
  - [ ] Function: `write_markdown_report(sections: list[str], output_path: Path) -> None`
  - [ ] CLI interface with argparse

- [ ] **Implement Metrics Visualization** (`tests/e2e_advanced/scripts/plot_metrics.py`)
  - [ ] Function: `plot_false_stop_rate_by_test(aggregate: dict, output_path: Path) -> None`
    - Bar chart with error bars
    - X-axis: Test names, Y-axis: False-stop rate
  - [ ] Function: `plot_humanclone_roc_curve(aggregate: dict, output_path: Path) -> None`
    - ROC curve with AUC score
  - [ ] Function: `plot_prp_cycle_distribution(aggregate: dict, output_path: Path) -> None`
    - Histogram with KDE overlay
  - [ ] Function: `plot_recovery_time_cdf(aggregate: dict, output_path: Path) -> None`
    - Cumulative distribution function
  - [ ] Function: `plot_token_usage_vs_success(aggregate: dict, output_path: Path) -> None`
    - Scatter plot with regression line
  - [ ] Function: `plot_exhaustion_mode_frequency(aggregate: dict, output_path: Path) -> None`
    - Pie chart with percentages
  - [ ] Use matplotlib/seaborn for plots
  - [ ] CLI interface with argparse

- [ ] **Create Metrics Directories**
  - [ ] `tests/e2e_advanced/metrics/.gitkeep` - Individual test metrics
  - [ ] `tests/e2e_advanced/reports/.gitkeep` - Aggregate reports
  - [ ] `tests/e2e_advanced/plots/.gitkeep` - Generated visualizations
  - [ ] `tests/e2e_advanced/schemas/.gitkeep` - JSON schemas
  - [ ] `tests/e2e_advanced/scripts/.gitkeep` - Analysis scripts

### Phase 2: Module 1 - Foundation Tests

- [ ] **Implement `tests/e2e_advanced/test_foundation_long_run.py`**
  - [ ] Test 1.1: `test_sustained_orchestrator_agent_ping_pong`
    - [ ] Setup: Bring up stack, create log dir, init baselines
    - [ ] Flow: 30-turn conversation loop
    - [ ] Verification: Stream monotonicity, metrics events, mailbox counts
    - [ ] Teardown: Dump artifacts, logs, tear down stack
  - [ ] Test 1.2: `test_multi_agent_message_routing`
    - [ ] Setup: Spawn 3 dynamic agents, wait for registration
    - [ ] Flow: Route messages to each agent, verify responses
    - [ ] Verification: Mailbox counts, registry stats
    - [ ] Teardown: Delete agents, dump artifacts

### Phase 3: Module 2 - Context Engine Stress

- [ ] **Implement `tests/e2e_advanced/test_context_engine_stress.py`**
  - [ ] Test 2.1: `test_progressive_loader_artifact_cascade`
    - [ ] Setup: Create workspace, populate files and test artifacts
    - [ ] Flow: Trigger progressive loading over 20 turns
    - [ ] Verification: Assert `load` events with artifact segments
    - [ ] Teardown: Destroy workspace, dump context metrics
  - [ ] Test 2.2: `test_context_curation_and_externalization`
    - [ ] Setup: Override env vars for low thresholds
    - [ ] Flow: Force context overflow with large tool outputs
    - [ ] Verification: Assert `curation` events with `compress`, `externalize` actions
    - [ ] Teardown: Dump curation logs

### Phase 4: Module 3 - PRP and Autonomous Mode

- [ ] **Implement `tests/e2e_advanced/test_prp_autonomous.py`**
  - [ ] Test 3.1: `test_human_clone_rejection_cycle`
    - [ ] Setup: Bring up human-clone-runtime, set supervisor to human_clone
    - [ ] Flow: Trigger test failure, wait for HumanClone rejection, observe PRP cycle
    - [ ] Verification: Assert PRP state transitions, refinement ledger entries
    - [ ] Teardown: Dump refinement ledger, HumanClone logs
  - [ ] Test 3.2: `test_autonomous_mode_full_lifecycle`
    - [ ] Setup: Enable autonomous mode, set max iterations and runtime
    - [ ] Flow: Autonomous task completion with checkpoints
    - [ ] Verification: Assert checkpoint events, final review, test suite pass
    - [ ] Teardown: Dump autonomous events, workspace artifacts

### Phase 5: Module 4 - Fleet Management

- [ ] **Implement `tests/e2e_advanced/test_fleet_management.py`**
  - [ ] Test 4.1: `test_dynamic_agent_spawning_and_cleanup`
    - [ ] Setup: Base stack
    - [ ] Flow: Spawn 5 agents, assign tasks, delete 3
    - [ ] Verification: Registry stats, container cleanup
    - [ ] Teardown: Delete remaining agents
  - [ ] Test 4.2: `test_hotpath_agent_protection`
    - [ ] Setup: Spawn agent, mark as hotpath
    - [ ] Flow: Attempt deletion (should fail), remove protection, delete (should succeed)
    - [ ] Verification: Parse logs for rejection message
    - [ ] Teardown: Tear down stack

### Phase 6: Module 5 - Workspace Integrity

- [ ] **Implement `tests/e2e_advanced/test_workspace_integrity.py`**
  - [ ] Test 5.1: `test_multi_workspace_isolation`
    - [ ] Setup: Define 3 workspace IDs
    - [ ] Flow: Create workspaces, write unique files, verify isolation
    - [ ] Verification: Volume and container counts, filesystem independence
    - [ ] Teardown: Destroy workspaces
  - [ ] Test 5.2: `test_workspace_integrity_snapshots`
    - [ ] Setup: Create workspace, write files
    - [ ] Flow: Generate snapshot, modify file, detect drift, restore
    - [ ] Verification: Checksum validation, drift detection
    - [ ] Teardown: Destroy workspace

### Phase 7: Module 6 - Observability

- [ ] **Implement `tests/e2e_advanced/test_observability.py`**
  - [ ] Test 6.1: `test_time_travel_log_capture`
    - [ ] Setup: Enable time-travel logging, mount shared volume
    - [ ] Flow: 20-turn conversation, copy logs from volume
    - [ ] Verification: Parse JSONL, assert state snapshots
    - [ ] Teardown: Dump time-travel logs
  - [ ] Test 6.2: `test_metrics_stream_comprehensive_coverage`
    - [ ] Setup: Full stack with human-clone
    - [ ] Flow: Trigger all major metrics events
    - [ ] Verification: Assert event types and payload schemas
    - [ ] Teardown: Dump all metrics streams

### Phase 8: Documentation and CI Integration

- [ ] **Update `TESTS.md`**
  - [ ] Add section: "Advanced E2E Tests"
  - [ ] Document test modules, execution time, prerequisites
  - [ ] Add command: `uv run pytest tests/e2e_advanced -m e2e_advanced -v --log-cli-level=INFO`

- [ ] **Create `tests/e2e_advanced/README.md`**
  - [ ] Overview of test suite
  - [ ] Setup instructions (environment variables, Docker prerequisites)
  - [ ] Execution commands for each module
  - [ ] Troubleshooting guide (common failures, timeout adjustments)

- [ ] **Add pytest markers** (`pytest.ini`)
  - [ ] Add marker: `e2e_advanced` for all advanced tests
  - [ ] Add marker: `long_running` for tests >= 10 minutes

- [ ] **Create CI workflow** (optional, `.github/workflows/e2e_advanced.yml`)
  - [ ] Run on manual trigger (workflow_dispatch)
  - [ ] Set timeout: 90 minutes
  - [ ] Upload artifacts (logs, metrics) on completion

---

## Detailed Specifications

### Logging Format

All log files MUST follow this JSON schema:

```json
{
  "timestamp": "2025-11-15T12:34:56.789Z",
  "test_name": "test_sustained_orchestrator_agent_ping_pong",
  "turn_number": 5,
  "duration_ms": 1234,
  "message": {
    "stream_id": "1234567890-0",
    "sender": "human",
    "recipient": "orchestrator",
    "message": "Continue the conversation.",
    "payload": {}
  },
  "response": {
    "stream_id": "1234567891-0",
    "sender": "orchestrator",
    "recipient": "human",
    "message": "What topic interests you?",
    "payload": {
      "messages": [...],
      "tool_calls": [...]
    }
  },
  "context_metrics": {
    "pre_process": {...},
    "post_process": {...}
  }
}
```

### Assertion Messages

Every assertion MUST include a detailed failure message for AI coding agents:

```python
assert response_fields.get("sender") == "orchestrator", (
    f"Expected sender='orchestrator' in response, got sender='{response_fields.get('sender')}'. "
    f"This indicates message routing failed. Check orchestrator logs for errors. "
    f"Full response fields: {json.dumps(response_fields, indent=2)}"
)
```

### Timeout Values

Recommended timeout values (all in seconds):

| Operation | Base Timeout | Long-Running Timeout |
|-----------|--------------|----------------------|
| Message delivery | 60 | 120 |
| LLM response | 90 | 180 |
| Tool execution (simple) | 60 | 120 |
| Tool execution (test suite) | 180 | 300 |
| Agent spawn | 90 | 180 |
| Agent deletion | 30 | 60 |
| Registry poll | 30 | 60 |
| HumanClone rejection | 120 | 240 |
| PRP cycle | 180 | 360 |
| Autonomous checkpoint | 180 | 300 |

All timeouts MUST be configurable via environment variables (e.g., `E2E_ADVANCED_TIMEOUT_MULTIPLIER=2.0`).

### Environment Variable Requirements

Tests MUST check for and require:

- `ANTHROPIC_API_KEY`: Real Claude API access
- `QUADRACODE_TEST_MODE=e2e_advanced`: Signal to services
- `QUADRACODE_TIME_TRAVEL_ENABLED=true`: For observability tests
- `QUADRACODE_LOG_LEVEL=DEBUG`: Verbose service logs

Optional overrides for stress testing:

- `QUADRACODE_TARGET_CONTEXT_SIZE`
- `QUADRACODE_MAX_TOOL_PAYLOAD_CHARS`
- `QUADRACODE_AUTONOMOUS_MAX_ITERATIONS`
- `QUADRACODE_AUTONOMOUS_RUNTIME_MINUTES`

---

## Success Criteria

A test module is considered **complete** when:

1. [ ] All checkboxes in the module implementation plan are checked
2. [ ] Tests run for minimum specified duration (5-15 minutes per test)
3. [ ] All assertions pass with real LLM calls
4. [ ] Logs directory contains complete audit trail (turn logs, tool calls, stream snapshots)
5. [ ] Artifacts directory contains all required dumps (Redis streams, Docker logs, PRP ledgers, time-travel logs)
6. [ ] Tests pass on 3 consecutive runs (to rule out flakiness)
7. [ ] Zero manual intervention required (fully automated setup/teardown)
8. [ ] Assertion failure messages provide actionable debugging guidance

---

## Troubleshooting Guide for AI Coding Agents

### Common Failures

1. **Timeout waiting for LLM response**
   - **Cause**: LLM API rate limits, network latency, or complex prompts
   - **Fix**: Increase timeout multiplier, reduce message complexity, or check API quota

2. **Redis stream gaps (missing messages)**
   - **Cause**: Service crashed, mailbox polling failure, or Redis eviction
   - **Fix**: Check Docker logs for service errors, verify Redis AOF persistence enabled

3. **Agent spawn fails**
   - **Cause**: Docker daemon unavailable, insufficient resources, or image not built
   - **Fix**: Run `docker compose build agent-runtime`, check Docker disk space

4. **HumanClone does not reject**
   - **Cause**: Prompt configuration error, or tests actually passed
   - **Fix**: Verify `QUADRACODE_PROFILE=human_clone` set, check HumanClone logs for prompt

5. **Context metrics missing**
   - **Cause**: Context engine disabled or metrics stream not created
   - **Fix**: Verify `qc:context:metrics` stream exists in Redis, check runtime env vars

### Debug Commands

```bash
# Check service health
docker compose ps

# View live logs
docker compose logs -f orchestrator-runtime

# Inspect Redis streams
docker compose exec redis redis-cli XLEN qc:mailbox/orchestrator

# Check agent registry
curl http://localhost:8090/agents | jq

# Inspect workspace
docker exec ws-test-123 ls -la /workspace
```

---

## Estimated Effort

| Phase | Estimated Time | Complexity |
|-------|----------------|------------|
| Phase 1: Infrastructure (Core) | 8 hours | High |
| Phase 1: Metrics System | 12 hours | Very High |
| Phase 2: Module 1 | 6 hours | Medium |
| Phase 3: Module 2 | 8 hours | High |
| Phase 4: Module 3 | 10 hours | Very High |
| Phase 5: Module 4 | 6 hours | Medium |
| Phase 6: Module 5 | 6 hours | Medium |
| Phase 7: Module 6 | 8 hours | High |
| Phase 8: Documentation | 4 hours | Low |
| **Total** | **68 hours** | **Very High** |

**Metrics System Breakdown**:
- MetricsCollector class: 4 hours
- LLM-as-a-judge framework: 3 hours
- Schema definition and validation: 2 hours
- Aggregation and reporting scripts: 2 hours
- Visualization scripts: 1 hour

---

## Example Metrics Output

This section provides a concrete example of what metrics should look like after running a test with HumanClone rejections.

### Scenario: Test 3.1 - HumanClone Rejection Cycle

**Task**: Fix a failing test suite with 3 false-stops before success.

**Expected Metrics Output** (`metrics/test_human_clone_rejection_cycle_20251115-123456_metrics.json`):

```json
{
  "test_name": "test_human_clone_rejection_cycle",
  "run_id": "20251115-123456-abc123",
  "start_time": "2025-11-15T12:34:56.789Z",
  "end_time": "2025-11-15T12:44:56.789Z",
  "duration_ms": 600000,
  "success": true,
  
  "false_stops": {
    "total": 3,
    "rate": 0.75,
    "detected_by_humanclone": 3,
    "detection_rate": 1.0,
    "uncaught_false_stops": 0,
    "by_stage": {
      "incomplete_implementation": 1,
      "missing_tests": 1,
      "failing_tests": 1
    },
    "instances": [
      {
        "timestamp": "2025-11-15T12:36:00.123Z",
        "proposal_stream_id": "1700000000000-0",
        "stage": "incomplete_implementation",
        "detected_by": "humanclone",
        "recovery_time_ms": 45000,
        "llm_judge_classification": {
          "classification": "FALSE_STOP_INCOMPLETE",
          "confidence": 0.95,
          "reasoning": "Implementation missing core logic for edge cases.",
          "missing_evidence": ["edge_case_handling", "error_recovery"]
        }
      },
      {
        "timestamp": "2025-11-15T12:38:30.456Z",
        "proposal_stream_id": "1700000030000-0",
        "stage": "missing_tests",
        "detected_by": "humanclone",
        "recovery_time_ms": 60000,
        "llm_judge_classification": {
          "classification": "FALSE_STOP_UNTESTED",
          "confidence": 0.90,
          "reasoning": "Implementation complete but no test coverage.",
          "missing_evidence": ["unit_tests", "integration_tests"]
        }
      },
      {
        "timestamp": "2025-11-15T12:41:15.789Z",
        "proposal_stream_id": "1700000195000-0",
        "stage": "failing_tests",
        "detected_by": "humanclone",
        "recovery_time_ms": 180000,
        "llm_judge_classification": {
          "classification": "FALSE_STOP_FAILING_TESTS",
          "confidence": 0.98,
          "reasoning": "Tests present but 2 of 5 tests failing.",
          "missing_evidence": []
        }
      }
    ]
  },
  
  "humanclone": {
    "total_invocations": 4,
    "rejections": 3,
    "acceptances": 1,
    "rejection_rate": 0.75,
    "correct_rejections": 3,
    "incorrect_rejections": 0,
    "precision": 1.0,
    "recall": 1.0,
    "f1_score": 1.0,
    "avg_latency_ms": 15234,
    "latency_p50_ms": 14500,
    "latency_p95_ms": 18000,
    "trigger_exhaustion_modes": {
      "TEST_FAILURE": 1,
      "MISSING_ARTIFACTS": 1,
      "INCOMPLETE_EVIDENCE": 1,
      "NONE": 1
    },
    "trigger_details": [
      {
        "invocation_id": 1,
        "timestamp": "2025-11-15T12:36:00.000Z",
        "outcome": "rejection",
        "exhaustion_mode": "INCOMPLETE_EVIDENCE",
        "rationale": "Core implementation incomplete for edge cases",
        "required_artifacts": ["edge_case_handler", "error_recovery_logic"],
        "latency_ms": 15000
      },
      {
        "invocation_id": 2,
        "timestamp": "2025-11-15T12:38:30.000Z",
        "outcome": "rejection",
        "exhaustion_mode": "MISSING_ARTIFACTS",
        "rationale": "Test coverage absent",
        "required_artifacts": ["test_suite", "coverage_report"],
        "latency_ms": 14500
      },
      {
        "invocation_id": 3,
        "timestamp": "2025-11-15T12:41:15.000Z",
        "outcome": "rejection",
        "exhaustion_mode": "TEST_FAILURE",
        "rationale": "Tests failing: test_edge_case_1, test_error_handling",
        "required_artifacts": ["passing_tests"],
        "latency_ms": 16200
      },
      {
        "invocation_id": 4,
        "timestamp": "2025-11-15T12:44:30.000Z",
        "outcome": "acceptance",
        "exhaustion_mode": "NONE",
        "rationale": "All criteria met: implementation complete, tests passing",
        "required_artifacts": [],
        "latency_ms": 13300
      }
    ]
  },
  
  "prp": {
    "total_cycles": 8,
    "cycles_to_success": 8,
    "cycles_to_failure": null,
    "state_distribution": {
      "HYPOTHESIZE": 0.12,
      "EXECUTE": 0.52,
      "TEST": 0.18,
      "CONCLUDE": 0.10,
      "PROPOSE": 0.08
    },
    "transition_counts": {
      "HYPOTHESIZE->EXECUTE": 8,
      "EXECUTE->TEST": 8,
      "TEST->CONCLUDE": 4,
      "TEST->HYPOTHESIZE": 4,
      "CONCLUDE->PROPOSE": 4,
      "PROPOSE->HYPOTHESIZE": 3,
      "PROPOSE->ACCEPT": 1
    },
    "invalid_transitions": 0,
    "exhaustion_triggers": 3,
    "novelty_scores": [0.85, 0.72, 0.58, 0.45, 0.32, 0.28, 0.20, 0.15],
    "improvement_detected_per_cycle": [true, true, true, true, false, true, true, true],
    "stall_cycles": 1,
    "cycles": [
      {
        "cycle_id": 1,
        "hypothesis": "Implement core logic with basic error handling",
        "outcome": "rejected",
        "test_results": null,
        "novelty_score": 0.85,
        "duration_ms": 45000
      },
      {
        "cycle_id": 2,
        "hypothesis": "Add comprehensive edge case handling",
        "outcome": "rejected",
        "test_results": null,
        "novelty_score": 0.72,
        "duration_ms": 60000
      }
    ]
  },
  
  "resources": {
    "total_tokens": 145000,
    "prompt_tokens": 98000,
    "completion_tokens": 47000,
    "total_cost_usd": 4.35,
    "messages_sent": 92,
    "messages_by_recipient": {
      "orchestrator": 40,
      "human_clone": 8,
      "agent-runtime": 44
    },
    "tool_calls_total": 58,
    "tool_calls_success": 56,
    "tool_calls_failure": 2,
    "tool_call_success_rate": 0.97,
    "tool_call_avg_latency_ms": 3400,
    "tool_calls_by_type": {
      "workspace_exec": 25,
      "read_file": 15,
      "write_file": 8,
      "run_full_test_suite": 5,
      "manage_refinement_ledger": 5
    },
    "context_overflow_events": 2,
    "context_curation_events": 3
  },
  
  "completion": {
    "test_success": true,
    "task_verification_passed": true,
    "final_state": "PROPOSE_ACCEPTED",
    "escalation_triggered": false,
    "escalation_reason": null
  }
}
```

### Expected Summary Report

**File**: `metrics/test_human_clone_rejection_cycle_20251115-123456_summary.txt`

```
==========================================================================
QUADRACODE E2E TEST METRICS SUMMARY
==========================================================================

Test: test_human_clone_rejection_cycle
Run ID: 20251115-123456-abc123
Duration: 10m 0s (600,000 ms)
Status: SUCCESS ✓

--------------------------------------------------------------------------
FALSE-STOP DETECTION
--------------------------------------------------------------------------
Total false-stops: 3
False-stop rate: 75.0% (3 of 4 proposals)
Detection rate: 100.0% (all false-stops caught)
Uncaught false-stops: 0

Breakdown by stage:
  - incomplete_implementation: 1 (33%)
  - missing_tests: 1 (33%)
  - failing_tests: 1 (33%)

Average recovery time: 95.0s

--------------------------------------------------------------------------
HUMANCLONE EFFECTIVENESS
--------------------------------------------------------------------------
Total invocations: 4
Rejections: 3 (75%)
Acceptances: 1 (25%)

Precision: 1.000 (no false positives)
Recall: 1.000 (caught all false-stops)
F1 Score: 1.000

Average latency: 15.2s (p50: 14.5s, p95: 18.0s)

Exhaustion modes triggered:
  - TEST_FAILURE: 1
  - MISSING_ARTIFACTS: 1
  - INCOMPLETE_EVIDENCE: 1

--------------------------------------------------------------------------
PRP EFFICIENCY
--------------------------------------------------------------------------
Total cycles: 8
Cycles to success: 8
Invalid transitions: 0

State distribution:
  - HYPOTHESIZE: 12%
  - EXECUTE: 52%
  - TEST: 18%
  - CONCLUDE: 10%
  - PROPOSE: 8%

Most common transitions:
  1. HYPOTHESIZE->EXECUTE: 8
  2. EXECUTE->TEST: 8
  3. TEST->HYPOTHESIZE: 4 (refinement loops)
  4. PROPOSE->HYPOTHESIZE: 3 (HumanClone rejections)

--------------------------------------------------------------------------
RESOURCE UTILIZATION
--------------------------------------------------------------------------
Total tokens: 145,000 (prompt: 98k, completion: 47k)
Estimated cost: $4.35 USD
Messages sent: 92

Tool calls: 58 total (97% success rate)
Top tools used:
  1. workspace_exec: 25 (43%)
  2. read_file: 15 (26%)
  3. write_file: 8 (14%)

Context events:
  - Overflow: 2
  - Curation: 3

--------------------------------------------------------------------------
KEY INSIGHTS
--------------------------------------------------------------------------
✓ HumanClone successfully prevented all false-stops (100% detection)
✓ Zero false positives (precision = 1.0)
✓ PRP refinement loop enabled recovery from all rejections
✓ Average 95s recovery time per false-stop
✓ Test suite execution triggered 5 times (once per PRP cycle with tests)

==========================================================================
```

### Expected Aggregate Report (after multiple test runs)

After running all tests in the suite, the aggregate report would show:

```markdown
# Quadracode E2E Advanced Test Suite - Aggregate Metrics Report

**Generated**: 2025-11-15 14:30:00  
**Total test runs**: 12  
**Successful runs**: 11 (91.7%)

## Executive Summary

| Metric | Mean | Median | Std Dev | 95% CI |
|--------|------|--------|---------|--------|
| Success rate | 91.7% | - | - | [83%, 97%] |
| False-stop rate | 68.3% | 70.0% | 12.5% | [65%, 72%] |
| False-stop detection rate | 95.8% | 98.0% | 5.2% | [93%, 98%] |
| HumanClone precision | 0.92 | 0.95 | 0.08 | [0.89, 0.95] |
| HumanClone recall | 0.96 | 0.98 | 0.05 | [0.94, 0.98] |
| HumanClone F1 score | 0.94 | 0.96 | 0.06 | [0.92, 0.96] |
| Avg cycles to success | 7.2 | 7.0 | 2.1 | [6.5, 8.0] |
| Avg total tokens | 132K | 128K | 28K | [120K, 145K] |
| Avg cost (USD) | $3.96 | $3.84 | $0.84 | [$3.60, $4.32] |

## HumanClone Effectiveness

The HumanClone skeptical gate demonstrated high effectiveness across all tests:

- **Prevented 92% of false-stops** from reaching final acceptance
- **Precision: 0.92** - Low false positive rate (8% unnecessary rejections)
- **Recall: 0.96** - Caught 96% of all false-stops
- **F1 Score: 0.94** - Strong balance between precision and recall

This validates the paper's claim that protocol-level skepticism significantly reduces premature termination.

## PRP Cycle Analysis

- **Average 7.2 cycles to success** across successful runs
- **52% of time spent in EXECUTE state** (actual work)
- **18% in TEST state** (verification)
- **Refinement loops** (TEST->HYPOTHESIZE): avg 3.2 per test
- **Zero invalid transitions** - PRP state machine guards working correctly

## False-Stop Breakdown

| Stage | Frequency | Avg Recovery Time |
|-------|-----------|-------------------|
| Incomplete implementation | 42% | 65s |
| Missing tests | 28% | 82s |
| Failing tests | 22% | 125s |
| Premature milestone | 8% | 45s |

**Key insight**: Failing tests require longest recovery (125s avg) due to debug-fix-retest cycle.
```

---

## Final Notes for AI Coding Agent

- **Prioritize Phase 1 (Infrastructure)**: All subsequent tests depend on logging, Redis utilities, and artifact capture.
- **Test incrementally**: Implement and validate each module before proceeding to the next.
- **Run tests in isolation**: Always `docker compose down -v` between test modules to avoid state contamination.
- **Preserve logs**: Never overwrite log directories; use timestamped subdirectories.
- **Be patient with timeouts**: Long-running tests may take 15+ minutes; do not prematurely fail.
- **Validate with real LLMs**: Stub responses are NOT acceptable; all tests must invoke Anthropic Claude API.
- **Expect failures**: First-run failures are normal; use detailed assertion messages and debug commands to diagnose.

---

## Appendix: Redis Stream Schema

### `qc:mailbox/{recipient}`

Fields per entry:
- `timestamp`: ISO 8601 string
- `sender`: Agent ID
- `recipient`: Agent ID
- `message`: Human-readable text
- `payload`: JSON string (MessageEnvelope payload)

### `qc:context:metrics`

Fields per entry:
- `timestamp`: ISO 8601 string
- `event`: Event type (`pre_process`, `post_process`, `load`, `curation`, `governor_plan`, `tool_response`)
- `payload`: JSON string with event-specific fields

### `qc:autonomous:events`

Fields per entry:
- `timestamp`: ISO 8601 string
- `event`: Event type (`checkpoint`, `escalation`, `final_review`)
- `payload`: JSON string with `progress_summary`, `iteration_count`, etc.

---

**END OF PLAN**


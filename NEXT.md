## URGENT — Out-of-Order Tasks For Continuation

This section lists prioritized items that should be implemented next, even if
they appear later in numbering. Keep telemetry minimal (no cost), focus on
demonstrating persistent progress, robust context engineering, explicit
exhaustion detection, and skepticism loops.

- [Complete] 8.5 False-Stop Detection & Mitigation Metrics
  - Add state counters: `false_stop_events`, `false_stop_mitigated` under `invariants` or a sibling `autonomy_counters` block.
  - Detect early halts: (a) `LLM_STOP` flagged by context engine, (b) "declared completion" while tests/artifacts contradict completion.
  - On false stop, auto-trigger HumanClone skepticism + PRP reset; increment counters; persist minimal entries to `prp_telemetry` and `time_travel_log`.
  - Tests: ensure early-stop cases are recorded and subsequent test execution clears the condition and increments `false_stop_mitigated`.

- [Complete] 11.5 Hotpath Service Agents & Orchestrator Guard
  - Extend agent registry schema: add `hotpath: bool` to agent records and registry API responses; default false.
  - Add tool ops to set/unset hotpath (extend `agent_management`), and ensure orchestrator never tears down `hotpath=True` agents; probe them before routing.
  - Add an invariant: hotpath agents remain resident; violations log to `prp_telemetry` and registry events.
  - Tests: spawn an agent, mark hotpath, verify no teardown on scale-down paths and presence in registry snapshots.

- [Complete] Skeptical Orchestrator Policy (mirror HumanClone)
  - Require skepticism gates on agent responses, not only orchestrator→HumanClone. Add a lightweight invariant: before acceptance, at least one skepticism/critique pass has occurred.
  - Track a minimal `skepticism_challenges` counter; no extra dashboards.

- [Complete] Paper Updates (keep for Arxiv/Paperswithcode)
  - Step 16 Delta 8 (AGI capability ladder mapping) must be completed: include a concise table mapping Quadracode capabilities and a short comparison to baselines.

Notes:
- Do not implement budget/cost features; token usage is sufficient for empirical reporting.
- Keep UI changes optional; CLI + JSONL logs are enough to support the paper.

## Core Architecture Transformations

<!-- FOCUS RESET — Publication-Oriented Scope

Trim UI/UX-heavy deliverables; emphasize long-horizon task persistence,
robust per-cycle context engineering, explicit exhaustion detection, and
skeptical review loops (HumanClone + Orchestrator). Omit cost accounting
and keep telemetry minimal (only the signals required for empirical results).

-->

### 1. [Complete] Implement Stateful Meta-Cognitive Infrastructure

- Extend `QuadraCodeState` TypedDict with meta-cognitive state fields: `is_in_prp`, `prp_cycle_count`, `refinement_ledger: List[RefinementLedgerEntry]`, and `exhaustion_mode: ExhaustionEnum`
- Create `RefinementLedgerEntry` BaseModel with fields: `cycle_id`, `timestamp`, `hypothesis`, `status`, `outcome_summary`, `exhaustion_trigger`, `test_results`
- Implement ledger persistence in graph checkpoints, ensuring memory survives across sessions
- Build ledger serialization into the context engine to inject formatted memory blocks into orchestrator prompts

### 1.5. [Complete] Implement PRP State Machine as a Formal Finite State Automaton (FSA)

- **Implementation Details:** Define a formal FSA for the Perpetual Refinement Protocol (PRP) in `quadracode-runtime/src/quadracode_runtime/state.py` (or adjacent module) using either `automata-lib` or a custom Pydantic-driven transition model. Explicitly encode states such as HYPOTHESIZE, TEST, EXECUTE, CONCLUDE, and PROPOSE with transition guards bound to exhaustion modes and HumanClone triggers.
- **Runtime Enforcement:** Integrate validation so the runtime enforces the FSA transitions, surfacing structured telemetry events whenever an invalid transition attempt occurs.
- **Rationale:** This formalizes the autonomy loop, aligning with Gemini's state-machine emphasis and Codex Directive 11. It supplies provable invariants (e.g., deadlock freedom) that elevate Quadracode from heuristic orchestration to a mathematically grounded cognitive architecture—essential for future Arxiv submissions targeting Systems-2 researchers.

### CONTEXT UPDATE

- Step 1 and Delta 1.5 are complete: `QuadraCodeState` now carries PRP/ledger fields, ledger serialization works through checkpoints, and the PRP FSA enforces guarded transitions with telemetry.
- Context engine nodes and driver already consume the new state; PRP transitions fire during govern/tool/post stages, and the refinement ledger summary is injected into the system prompt.
- Tests currently cover the new state machine (`tests/test_prp_state_machine.py`) and updated metrics expectations; consider expanding coverage once HumanClone transition hooks land.
- Pending: Step 2 requires refactoring HumanClone interactions into structured triggers, likely wiring into `apply_prp_transition` and adding validation middleware without regressing current ledger/telemetry behavior.

### 2. [Complete] Transform HumanClone from Conversational to Architectural Component

- Convert HumanClone responses into structured state transition triggers, not conversational messages
- Implement `prp_trigger_check` graph node that intercepts HumanClone messages and programmatically injects tool calls
- Define JSON/YAML contract schema for orchestrator-HumanClone protocol with fields for exhaustion_mode, cycle_iteration, required_artifacts
- Add schema validation middleware that re-queues malformed messages for self-correction before HumanClone processing

#### Context Update — Step 2 deliverables
- HumanClone replies now marshal through `HumanCloneTrigger` contracts; malformed messages bounce with structured errors before the runtime runs.
- `prp_trigger_check` node runs ahead of context pre-processing, backfilling PRP transitions and emitting `hypothesis_critique` tool calls so telemetry stays consistent.
- `QuadraCodeState` carries `human_clone_requirements` and `human_clone_trigger`, so downstream nodes can enforce artifact production per cycle.
- `parse_human_clone_trigger` in `quadracode_runtime/prp.py` handles both JSON and fenced YAML, enabling prompt tweaks without runtime code changes.
- Tests cover trigger parsing, PRP updates, and validation behavior; run `uv run pytest quadracode-runtime/tests/test_prp_trigger_node.py -q` for fast regression checks.

### 3. [Complete] Build Exhaustion Taxonomy System

- Create `ExhaustionMode` enum with states: CONTEXT_SATURATION, RETRY_DEPLETION, TOOL_BACKPRESSURE, LLM_STOP, TEST_FAILURE, HYPOTHESIS_EXHAUSTED
- Thread exhaustion tracking through all context engine stages: pre_process, govern_context, handle_tool_response, post_process
- Implement exhaustion-aware state transitions where each mode triggers specific recovery strategies
- Add exhaustion code propagation to all tool responses and agent communications

### Delta 2: Edit Step 3 - Enhance Exhaustion Taxonomy System with Probabilistic Prediction

- **Edit Details:** Expand the ExhaustionMode enum to include a `PREDICTED_EXHAUSTION` state. Add a new submodule in `quadracode-runtime/src/quadracode_runtime/exhaustion_predictor.py` using simple ML (e.g., scikit-learn logistic regression via code_execution tool) trained on historical ledger data to forecast exhaustion probabilities during pre_process. If probability > 0.7, preemptively trigger a hypothesis refinement before full exhaustion.
- **Explanation:** Original step is reactive; this makes it proactive, aligning with Systems-2's anticipatory reasoning (e.g., counterfactual planning from Gemini). It adds a learning layer, preventing repetitive failures and demonstrating emergent intelligence. For AGI appeal, this showcases "predictive meta-cognition," a hot topic in cognitive architectures; it also generates data for empirical charts in the paper's Evaluation section, strengthening Arxiv/Paperswithcode submissions with quantifiable self-improvement metrics.

#### Context Update — Step 3 deliverables
- Exhaustion taxonomy now includes `predicted_exhaustion`, and `QuadraCodeState` tracks both probability estimates and recovery history so every node sees consistent fatigue telemetry.
- `quadracode_runtime/exhaustion_predictor.py` fits a lightweight logistic regression over the refinement ledger; the context engine consults it during pre/govern/tool/post stages, emits metrics, and automatically schedules recovery routines.
- PRP transitions accept predicted exhaustion, letting the orchestrator preemptively jump back to HYPOTHESIZE while logging remediation actions (e.g., curation, refinement, or resume hints).
- Autonomous tool events and Redis envelopes publish the active exhaustion mode/probability, giving UI, registry, and agents a shared signal without extra wiring.
- Regression coverage lands in `tests/test_exhaustion_system.py` (predictor + orchestration) and updated `tests/test_autonomous_mode.py` (payload propagation checks).

## Autonomous Testing & Validation Framework

### 4. [Complete] Mandate Test-Driven Refinement Cycles

- Create `run_full_test_suite` tool that auto-discovers test commands via workspace analysis (package.json, pyproject.toml, Makefile)
- Enforce test execution before every `request_final_review` submission to HumanClone
- Implement test result parsing to extract PASS/FAIL signals and coverage metrics
- Build automatic remediation branching when tests fail, spawning specialized debugger agents

### 4.5 [Complete] Integrate Property-Based Testing for Hypothesis Validation

- **Addition Details:** After mandating test-driven cycles, add integration with Hypothesis (Python lib) or similar for generating adversarial test cases automatically during the TESTING state. Update the orchestrator prompt to require calling a new `generate_property_tests` tool that uses code_execution to run property-based checks (e.g., "for all inputs, output is idempotent"). Persist results in the refinement_ledger with failure examples.
- **Explanation:** Builds on Codex's formal invariants (Directive 11) and Claude's test enforcement, but shifts from unit/E2E tests to generative ones, which probe for edge cases autonomously. This appeals to AGI recruiters by mimicking "exploratory reasoning" in Systems-2, where agents don't just verify but discover flaws. For the paper, this enables a "Robustness to Uncertainty" subsection with benchmarks, making the system more submit-worthy by addressing emergent behavior limitations (Section 7.2 in original paper).


### Context Update — Autonomous Testing & Validation
- Step 4 is complete: regression suites run via `run_full_test_suite`, property tests run via `generate_property_tests`, and both telemetry streams update the refinement ledger + PRP exhaustion signals automatically.
- HumanClone now rejects final reviews unless both test artifacts are attached; orchestrator prompt enforces running the property generator during the PRP TEST phase.
- Next up is Workspace Integrity Management (Step 5); no tooling exists yet, so expect to introduce snapshot/diff/restore primitives plus checksum validation hooks.

### 5. [Complete] Implement Workspace Integrity Management

- Create workspace snapshot system triggered on HumanClone rejections and exhaustion events
- Build diffable patch generation for workspace state changes
- Implement automatic restoration paths for file corruption or workspace drift scenarios
- Add workspace validation checksums to prevent silent degradation

#### Context Update — Step 5 deliverables
- Workspace snapshots now persist as tarball archives with manifests, diffs, and telemetry stored under `workspace_snapshots/`. Every HumanClone rejection and exhaustion transition records a snapshot entry in `QuadraCodeState.workspace_snapshots` and surfaces metrics for observability.
- The runtime validates the active workspace on each exhaustion event, computing aggregate checksums and automatically restoring from the latest snapshot if drift or corruption is detected. Validation status feeds `workspace_validation` so downstream nodes can gate tool calls.
- Snapshot metadata plus diff patches preserve history for Paper/AGI exports, while state serialization/deserialization ensures checkpoints keep the new integrity records stable between runs.

## Meta-Cognitive Tooling

### 6. [Complete] Create Hypothesis Management Tools

- Implement `manage_refinement_ledger` tool with operations: propose_hypothesis, conclude_hypothesis, query_past_failures
- Build novelty detection to prevent re-attempting failed hypotheses without new strategies
- Add hypothesis dependency tracking to understand causal chains of improvements
- Create hypothesis success prediction based on ledger history

### Delta 4: Edit Step 6 - Augment Hypothesis Management Tools with Causal Inference

- **Edit Details:** Extend `manage_refinement_ledger` with a new operation: `infer_causal_chain` (args: cycle_ids: List[int]). Use networkx (via code_execution) to build a directed graph of hypothesis dependencies from the ledger, inferring causal links (e.g., "Hypothesis A failure caused B"). Add novelty detection to also check for causal redundancy, blocking hypotheses that repeat upstream causes without intervention.
- **Explanation:** Original focuses on basic tracking; this adds inference, drawing from Gemini's causal engine (Step 12). It transforms the ledger into a knowledge graph, enabling Systems-2-style "why" reasoning over "what." AGI teams (e.g., at DeepMind) prioritize causality for general intelligence; this delta advances recruitment by positioning Quadracode as a proto-AGI substrate. Paper-wise, it justifies a new "Causal Meta-Learning" figure/diagram, enhancing theoretical depth for Arxiv.

#### Context Update — Step 6 deliverables
- `manage_refinement_ledger` now gates all hypothesis management with `propose_hypothesis`, `conclude_hypothesis`, `query_past_failures`, and `infer_causal_chain` operations available through QuadracodeTools.
- Runtime enforcement adds novelty detection (token-similarity + causal redundancy guards), dependency tracking, and per-entry success probability predictions persisted on each ledger record.
- NetworkX-driven causal inference builds directed graphs from ledger dependencies, stores `causal_links`, and feeds structured summaries back into the prompt stream via system updates.
- Coverage: `quadracode-runtime/tests/test_manage_refinement_ledger.py`; execute `uv run pytest quadracode-runtime/tests/test_manage_refinement_ledger.py -q` for fast validation.

### 7. [Complete] Implement Self-Critique Infrastructure

- Replace `autonomous_critique` with hypothesis-driven critique system
- Build critique-to-hypothesis translation pipeline that converts qualitative feedback into testable improvements
- Implement critique categorization (code quality, architecture, test coverage, performance)
- Add critique severity scoring to prioritize refinement efforts

#### Context Update — Step 7 deliverables
- `hypothesis_critique` replaces the legacy critique tool, enforcing category + severity capture and wiring every entry directly to refinement ledger metadata and the critique backlog for prioritisation.
- The new translation pipeline (`quadracode_runtime/critique.py`) converts qualitative feedback into actionable improvement directives and derived tests, persisting both on the active hypothesis entry.
- HumanClone rejections now emit structured critiques with inferred categories/severity, ensuring PRP transitions immediately receive backlog updates and severity telemetry.
- Regression coverage added via `quadracode-runtime/tests/test_prp_trigger_node.py` and the new `test_hypothesis_critique.py` to guard the trigger flow plus translation logic.

## Observability & Telemetry

### 8. [Complete] Build Real-Time Meta-Cognitive Observability

- Implement Redis Streams publishers for all autonomous events: checkpoints, escalations, critiques, hypotheses
<!-- De-emphasize UI dashboards for publication scope. Keep minimal metrics only. -->
<!-- - Create Streamlit dashboard with tabs for: Refinement Cycles, Exhaustion States, Hypothesis Ledger, Test Results -->
- Build loop depth visualization showing nested refinement cycles
- Add token usage tracking per hypothesis with stage-level summaries (per user directive—no monetary cost calculations)

#### Context Update — Step 8 deliverables
- Introduced a `MetaCognitiveObserver` that streams telemetry for cycles, exhaustion transitions, ledger updates, autonomous events, and test results. It persists stage-level token usage, loop depth, and tool-call counts per hypothesis cycle without computing monetary cost.
- Context engine stages now push real-time observability events (pre-process, govern, post-process, tool handling) and reuse predictive exhaustion signals to enrich Redis streams.
- Test suite/property test integrations emit structured entries that annotate the active cycle, while ledger operations hydrate the new cycle metric payloads.
- Documentation updated to clarify that per-hypothesis telemetry is token-based only, reflecting the new requirement to avoid cost estimation. Dashboards are optional and not required for paper submission.

### 8.5. [Complete] False-Stop Detection & Mitigation Metrics

- Detect “false stops” when the model halts early (LLM_STOP) or declares completion while tests/artifacts contradict completion
- Record counters on state: `false_stop_events`, `false_stop_mitigated` and emit a minimal metric on mitigation
- Treat context-window overflow conclusions as exhaustion, not completion; escalate to HumanClone automatically
- Include per-cycle “mitigation_succeeded” flag for empirical reporting

#### Context Update — Step 8.5 deliverables
- Added an `autonomy_counters` bucket with `false_stop_events`, `false_stop_pending`, `false_stop_mitigated`, and `skepticism_challenges`, persisted through checkpoints/serialization.
- Context engine now flags false stops when exhaustion detects `LLM_STOP` or when `request_final_review` is issued without passing tests or required artifacts. Violations trigger automatic PRP resets, HumanClone skepticism, telemetry, and time-travel entries.
- Successful suite/property test runs clear pending false stops and increment mitigation counters, enabling the new invariant-backed telemetry stream. Regression coverage: `quadracode-runtime/tests/test_false_stop_metrics.py`.

### 8.6. [Complete] Skeptical Orchestrator Policy (mirror HumanClone)

- Added automatic skepticism challenges on every tool response unless a prior challenge already satisfied the gate, guaranteeing at least one critique before `CONCLUDE/PROPOSE` transitions.
- New invariant `skepticism_gate` piggybacks on existing PRP guardrails: violations log to `prp_telemetry`/time-travel, and the gate resets whenever a new hypothesis cycle begins.
- False-stop detection, HumanClone rejections, and manual critiques all funnel through `record_skepticism_challenge`, incrementing the shared counter described in the URGENT note.
- Tests now assert the invariant via `quadracode-runtime/tests/test_invariants.py` (see `test_skepticism_gate_required_before_conclude`).

### 9. [Complete] Create Time-Travel Debugging System

- Implement append-only event log capturing all state transitions, tool calls, and decisions
- Build deterministic replay harness to reproduce any refinement cycle
- Create CLI tools for iteration-level debugging and state inspection
- Add differential analysis between cycles to identify improvement patterns

#### Context Update — Step 9 deliverables
- Introduced `TimeTravelRecorder`, automatically logging every stage, tool call, PRP transition, exhaustion update, and cycle snapshot into per-thread JSONL histories under `time_travel_logs/` while caching the latest windows in `state["time_travel_log"]`.
- Wired the recorder throughout the context engine, PRP state machine, and autonomous tool handling so each decision carries deterministic metadata (cycle id, PRP state, exhaustion mode).
- Added the `python -m quadracode_runtime.time_travel` CLI with `replay` and `diff` commands to inspect iterations and compute token/tool-call deltas between cycles.
- Documented the workflow in README’s Observability section, highlighting the new token-based telemetry and replay tooling.

### Delta 5: Add New Step 9.5 Under Observability & Telemetry - Implement Differential Privacy for Ledger Sharing

- **Addition Details:** Add a privacy module in `quadracode-ui/src/quadracode_ui/privacy.py` using libraries like diffprivlib (via code_execution) to anonymize sensitive ledger entries (e.g., mask proprietary code snippets) before dashboard rendering or export. Include an export tool for sanitized datasets to Paperswithcode.
- **Explanation:** Claude's observability is internal; this enables ethical sharing of meta-cognitive data. AGI research demands reproducible datasets, but privacy is a barrier— this resolves it, making Quadracode demo-ready for recruiters. For submissions, it allows uploading redacted longitudinal data (from Step 14), boosting visibility on Paperswithcode without IP risks, and addresses ethical limitations in the original paper's Discussion.

## Formal Verification Framework

### 10. [Complete] Implement Machine-Checkable Invariants

- Define formal properties: "every rejection triggers new tests", "no cycle without context update", "hypothesis novelty requirement"
- Build property-based testing suite using Hypothesis or similar frameworks
- Implement runtime assertion system that raises telemetry events on invariant violations
- Create SMT solver integration for proving termination properties

#### Context Update — Step 10 deliverables
- Added invariant tracking in runtime state under `invariants`: `needs_test_after_rejection`, `context_updated_in_cycle`, `violation_log`, `novelty_threshold`.
- PRP transitions now evaluate invariants; HumanClone rejections (PROPOSE→HYPOTHESIZE) set `needs_test_after_rejection=True`; transitions to `CONCLUDE`/`PROPOSE` emit `invariant_violation` if tests weren’t run or context wasn’t updated.
- Context engine `pre_process` marks `context_updated_in_cycle=True` each cycle.
- Recording test results (suite or property) clears the test requirement.
- Minimal structured telemetry is appended to `prp_telemetry` and `invariants.violation_log`; no additional dashboards.
- Tests added: `quadracode-runtime/tests/test_invariants.py` validates both invariants and the rejection→test clearing behavior.

### 11. [Commented Out] Cost-Aware Throttling System

<!-- Cost is out of scope for publication; tokens are tracked only for empirical clarity. -->
<!--
- Implement budget counters tracking LLM tokens, API calls, and compute time per cycle
- Create adaptive strategy selection based on remaining budget (model downgrading, batch processing)
- Build cost prediction models based on hypothesis complexity
- Implement emergency throttles that preserve infinite-loop contract while controlling expenses
-->

### 11.5. [Complete] Hotpath Service Agents & Orchestrator Guard

- Extend agent registry schema with `hotpath: bool` so the orchestrator never tears down service agents marked as critical
- Update `agent_management` to set/unset hotpath, and surface a simple `list_hotpath` view for audits
- Add orchestrator invariant: hotpath agents are kept alive and probed before dispatch; non-hotpath agents may scale down

#### Context Update — Step 11.5 deliverables
- Registry DB + schemas now persist the `hotpath` flag, expose `/agents/hotpath` listings, and block deletions unless explicitly forced. A dedicated `HotpathUpdateRequest` powers the new FastAPI endpoint.
- `agent_management` gained `mark_hotpath`, `clear_hotpath`, and `list_hotpath` operations backed by registry HTTP calls. Delete requests first query the registry and return a deterministic error if the target agent is resident.
- ContextEngine probes the registry before each `pre_process` pass, logging `hotpath_violation` telemetry and time-travel entries whenever a resident agent goes unhealthy, satisfying the invariants outlined in the URGENT note.
- Tests cover registry persistence (`quadracode-agent-registry/tests/test_hotpath_registry.py`) and the new tool flows (`quadracode-tools/tests/test_agent_management_hotpath.py`).

## Systems-2 Reasoning Implementation

### 12. [Complete] Create Deliberative Planning Module

- Implement multi-step reasoning chains with explicit intermediate states
- Build counterfactual reasoning system for hypothesis generation
- Create causal inference engine to understand improvement relationships
- Add probabilistic planning with uncertainty quantification

<!-- Defer MCTS integration to keep scope tight and empirical. -->
<!-- ### Delta 6: Edit Step 12 - Expand Deliberative Planning Module with Monte Carlo Tree Search (MCTS) Integration -->
<!-- (Deferred) -->

#### Context Update — Step 12 deliverables
- Introduced `DeliberativePlanner`, generating multi-step reasoning chains with explicit intermediate states sourced from the refinement ledger and current PRP signals; outputs feed the driver prompt via `deliberative_plan` and synopsis strings.
- Counterfactual hypothesis generation now persists under `counterfactual_register`, while a NetworkX causal engine summarizes dependency bottlenecks and accelerants for every cycle, wiring summaries into the runtime state and observability streams.
- Probabilistic planning computes success/uncertainty envelopes per cycle, surfaces risk factors tied to exhaustion/invariants, and logs telemetry so orchestrator prompts always include quantified planning confidence.
- Tests cover planner synthesis and ensure the context governor populates the new deliberative fields, keeping the greenfield module verified via `test_deliberative_planner.py` and `test_context_governor.py`.

### 13. [Complete] Implement Long-Term Memory Architecture

- Build episodic memory system storing complete refinement episodes
- Create semantic memory for abstracting patterns from episodes
- Implement memory consolidation that identifies recurring success patterns
- Add memory-guided hypothesis generation using learned strategies

<!-- Defer analogical reasoning for v2; not necessary for core demonstration. -->
<!-- ### Delta 7: Add New Step 13.5 - Embed Analogical Reasoning from Episodic Memory -->
<!-- (Deferred) -->

#### Context Update — Step 13 deliverables
- Added `long_term_memory.py` with episodic records, semantic pattern synthesis, and guidance frames. Concluded cycles now emit serialized episodes plus deterministic telemetry and time-travel entries.
- Memory consolidation scans the latest refinement ledger outcomes, derives strategy success rates, captures bottleneck risk signals, and persists semantic patterns + consolidation logs on state hydration.
- The driver prompt now includes a memory-guidance block so hypothesis proposals automatically reuse high-performing strategies and account for flagged risk signals.
- Governance and ledger nodes update guidance every cycle; new regression tests (`test_long_term_memory.py`, `test_context_governor.py`) exercise episodic capture, semantic projection, and prompt injection.

## Evaluation & Demonstration

### 14. Run Longitudinal Infinite Projects

- Execute 1000+ iteration runs on complex projects (compiler refactor, database engine, web framework)
- Capture minimal metrics: cycle count, token usage, hypothesis success rates, exhaustion/false-stop frequencies, mitigations
- Document emergent behaviors and self-discovered optimization strategies
- Create case studies showing autonomous capability evolution

### 15. Build Formal Benchmarks

- Create standardized test suites for meta-cognitive capabilities
- Implement comparison framework against other autonomous systems
- Build metrics for: recovery speed, hypothesis novelty, learning efficiency
- Develop AGI-readiness scoring based on Systems-2 criteria

## Paper & Documentation Updates

### 16. Reframe as Meta-Cognitive Architecture

- Retitle paper to emphasize autonomous meta-cognition and hypothesis-driven self-correction
- Replace "problem of motivation" with "problem of meta-cognitive drive"
- Add formal definitions of Systems-1 vs Systems-2 reasoning in context
- Include state machine diagrams showing cognitive state transitions

### Delta 8: Edit Step 16 - Enhance Paper Reframing with AGI Capability Ladder Mapping

- **Edit Details:** In reframing, add a new subsection mapping Quadracode to Chollet's ARC or Legg's AGI levels (e.g., "Level 3: Efficient Adaptation via Meta-Cognition"). Include a table comparing to baselines like Auto-GPT or LangChain, highlighting PRP's superiority in loop persistence.
- **Explanation:** Original reframing is conceptual; this adds benchmarks against AGI frameworks, directly from Systems-2 literature. This positions Quadracode as climbing the "AGI ladder" and makes the paper more compelling for Arxiv and Paperswithcode audiences.

### 17. Add Theoretical Foundations

- Add formal proofs of convergence properties
- Document relationship to AGI capability hierarchies

These steps transform Quadracode from an orchestration platform into a genuine meta-cognitive architecture with stateful reasoning, making it highly attractive to AGI and Systems-2 research teams.

<!-- OSS release automation is out of scope for core demo; manual release is fine. -->
<!-- ### Delta 9: Step 18 - OSS Release Pipeline (Deferred) -->

### Theory Updates — for quadracode_paper.md

- Add a formal definition of “False Stop” vs. “Exhaustion,” tied to PRP states and tests/artifacts
- Define mitigation rate metric and report methodology based on the minimal telemetry
- Specify “Skeptical Orchestrator” policy mirroring HumanClone skepticism (challenge requirement before acceptance)
- Document Hotpath agents as a service-residency constraint in the orchestration model (registry flag + invariant)

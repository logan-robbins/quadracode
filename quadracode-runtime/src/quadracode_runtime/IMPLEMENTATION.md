# Quadracode Runtime (`quadracode_runtime`) Implementation

This document provides a detailed technical overview of the `quadracode-runtime` module, the core engine powering the Quadracode multi-agent system. It outlines the architecture, key components, and data flow, with a focus on the mechanisms that enable autonomous, meta-cognitive, and resilient agent behavior.

## Core Architecture

The `quadracode-runtime` is built upon **LangGraph**, a library for constructing stateful, multi-agent applications as cyclical graphs. The central architectural pattern is a state machine where a comprehensive state object, `QuadraCodeState`, is passed between nodes. Each node represents a distinct cognitive function (e.g., planning, tool execution, context management) and can modify the state before passing it to the next node.

This architecture enables:
- **Stateful Execution**: The entire history of the conversation, tool calls, and internal reasoning is maintained within the state object, allowing for complex, long-running tasks.
- **Modularity**: Each cognitive function is encapsulated in its own node, making the system easy to extend and maintain.
- **Observability**: The explicit state transitions and append-only logging provide deep insights into the agent's behavior for debugging and analysis.

## Key Components and Data Structures

### 1. `QuadraCodeState`: The Central State Object

Defined in `state.py`, `QuadraCodeState` is a comprehensive `TypedDict` that serves as the single source of truth for the entire runtime. It aggregates several specialized state definitions:

- **`RuntimeState`**: Contains the basic elements for autonomous operation, including message history, task goals, and operational limits.
- **`ContextEngineState`**: Manages all aspects of the agent's context window, working memory, and external memory systems. It is the foundation for the **Adaptive Contextual Engagement (ACE)** and **Memory-Action (MemAct)** frameworks. Key fields include `context_segments`, `memory_checkpoints`, and metrics like `context_quality_score`.
- **`QuadraCodeState`**: The top-level state, which adds meta-cognitive signals and data structures for the **Perpetual Refinement Protocol (PRP)**. This includes the `refinement_ledger`, `prp_state`, and `exhaustion_mode`.

### 2. The Perpetual Refinement Protocol (PRP)

The PRP is the core meta-cognitive loop of the Quadracode system, enabling it to move beyond simple reactive behavior and engage in deliberate problem-solving. It is implemented as a finite state machine (FSM) governed by the `PRPStateMachine` in `state.py`.

- **Triggering Condition (Exhaustion)**: The PRP is activated when the runtime detects a state of **Exhaustion**, represented by the `ExhaustionMode` enum. Exhaustion occurs when the agent can no longer make forward progress, due to conditions like `TEST_FAILURE`, `RETRY_DEPLETION`, or `CONTEXT_SATURATION`.

- **PRP States**: The FSM transitions between several `PRPState`s:
    1.  `HYPOTHESIZE`: Generate a new plan or theory for how to overcome the exhaustion.
    2.  `EXECUTE`: Carry out the plan.
    3.  `TEST`: Validate the results of the execution.
    4.  `CONCLUDE`: Synthesize the findings.
    5.  `PROPOSE`: Package the conclusion for review (e.g., by a HumanClone agent).

- **`RefinementLedgerEntry`**: Every cycle through the PRP is meticulously logged in the `refinement_ledger` as a `RefinementLedgerEntry`. This Pydantic model captures the hypothesis, outcome, strategy, novelty score, and causal links, providing a rich, auditable history of the agent's reasoning.

### 3. Context Engineering (ACE/MemAct)

The runtime features an advanced context management system designed to overcome the limitations of finite context windows. This system, governed by the `ContextEngineState`, dynamically curates the most relevant information for the task at hand.

- **`ContextSegment`**: The context window is modeled as a collection of `ContextSegment`s, each with metadata for priority, token count, and decay rate.
- **Progressive Loading**: The `ProgressiveLoader` (`progressive_loader.py`) dynamically loads and unloads context segments based on a predefined `context_hierarchy`, ensuring the most critical information is always available.
- **Memory Checkpoints**: The system can persist the entire context state to disk as a `MemoryCheckpoint`, allowing for long-term memory and state restoration.
- **Context Governor**: The `ContextGovernor` (`context_governor.py`) acts as a final gatekeeper, assembling the optimal prompt from the available context segments based on a dynamic plan, ensuring prompt coherence and relevance.

### 4. Observability and Time-Travel Debugging

Deep observability is a first-class citizen in the runtime.

- **`TimeTravelRecorder`**: Defined in `time_travel.py`, this singleton class captures a fine-grained, append-only JSONL log of every significant event, including stage transitions, tool calls, and state updates. This allows for the complete, deterministic replay of any PRP cycle for debugging.
- **`Observability`**: The `observability.py` module provides a pub/sub mechanism (`MetaObserver`) for broadcasting high-level events (e.g., test results, cycle snapshots) to external monitoring systems.
- **Metrics**: The runtime tracks a wide array of metrics, from context quality and compression ratios to the frequency of false-stop events and skepticism challenges, providing a quantitative basis for analyzing agent performance.

### 5. Workspace Integrity

To ensure reliability and prevent unintended side effects, the `WorkspaceIntegrityManager` (`workspace_integrity.py`) provides robust tools for managing the agent's workspace (which can be a host directory or a Docker volume).

- **Snapshotting**: At critical junctures, the manager can capture a `tar.gz` archive of the workspace, accompanied by a detailed JSON manifest of all files and their SHA256 checksums.
- **Validation**: The checksum of the live workspace can be compared against a trusted snapshot's checksum to detect any drift or corruption.
- **Auto-Restoration**: If validation fails, the manager can automatically restore the workspace to the last known-good state from its archive.

## Control Flow and Graph Operation

1.  **Initialization**: A `QuadraCodeState` object is created with default values using `make_initial_context_engine_state`.
2.  **Node Execution**: The state object is passed to a node in the graph. The node executes its logic, which may involve calling tools, updating memory, or generating messages.
3.  **State Modification**: The node's logic modifies the `QuadraCodeState` object. For instance, `record_test_suite_result` updates test outcomes and may change the `exhaustion_mode`.
4.  **Conditional Routing**: After a node executes, conditional edges in the graph evaluate the updated state to decide which node to execute next. This is how the PRP loop is implemented: an `exhaustion_mode != "none"` condition routes the flow into the refinement subgraph.
5.  **PRP Transition**: Within the PRP loop, the `apply_prp_transition` function is called to move the `prp_state` between `HYPOTHESIZE`, `EXECUTE`, etc., ensuring all state-change guards are met.
6.  **Logging**: Throughout this process, the `TimeTravelRecorder` and `MetaObserver` are called to log events and state changes, creating a complete audit trail.
7.  **Termination**: The graph continues to execute until a terminal condition is met, such as reaching a solution, hitting an iteration limit, or being halted by a user.

This combination of a centralized state object, a meta-cognitive FSM, advanced context management, and deep observability allows the `quadracode-runtime` to support sophisticated, autonomous agent workflows that are both powerful and analyzable.

# Quadracode Runtime Implementation

The `quadracode-runtime` is the core package of the Quadracode ecosystem, providing the shared foundation for both the orchestrator and the agents. It is a sophisticated, LangGraph-based framework that includes a wide range of components for context management, autonomous operation, and tool usage.

## Architectural Overview

The runtime is designed as a modular and extensible framework. Its key architectural principles include:

- **Stateful Graph Execution**: The runtime is built around a `langgraph` state machine, where the `QuadraCodeState` serves as the central, evolving state object.
- **Context Engineering**: A significant portion of the runtime is dedicated to the `ContextEngine`, a sophisticated system for managing the context provided to the language models. This includes components for progressive loading, curation, scoring, and reduction of context.
- **Autonomous Operation**: The runtime has first-class support for autonomous operation, with a detailed set of protocols and components for managing long-running, multi-step tasks without human intervention.
- **Pluggable Tools**: The runtime includes a flexible tool loading system that can aggregate tools from local definitions, shared packages, and remote MCP servers.
- **Observability**: The runtime is instrumented with a comprehensive metrics and observability system, which provides detailed telemetry on the internal workings of the system.
  This includes a time-travel logger that records JSONL event streams for replay and diffing, using non-blocking background writes to remain safe under LangGraph’s ASGI-based dev server.

## Core Components

### 1. Context Engine (`nodes/`)

The `ContextEngine` is the heart of the runtime's context management system. It is a collection of nodes that work together to maintain a high-quality, relevant, and size-constrained context. Its key components include:

- **`ContextEngine`**: The high-level coordinator that orchestrates the entire context engineering lifecycle.
- **`ContextCurator`**: An implementation of the MemAct framework that applies a set of operations (e.g., retain, compress, summarize, externalize, isolate) to the context segments to keep the context size within a target limit while safely offloading archival content to external storage when enabled.
- **`ContextScorer`**: An implementation of the ACE framework that evaluates the quality of the context based on a set of heuristics.
- **`ProgressiveContextLoader`**: A component that loads context artifacts on demand, based on the current needs of the task.
- **`ContextReducer`**: A utility for summarizing and condensing large context segments.

### 2. Autonomous Operation (`autonomous.py`, `critique.py`, `prp_trigger.py`)

The runtime provides a rich set of components to support autonomous operation. These include:

- **`autonomous.py`**: A module for processing the outputs of autonomous tools and updating the system's state accordingly.
- **`critique.py`**: A module for processing and translating hypothesis-driven critiques into actionable next steps.
- **`prp_trigger.py`**: A graph node that converts responses from the HumanClone into PRP (Plan-Refine-Play) triggers, which drive the refinement loop.

### 3. Graph and Driver (`graph.py`, `nodes/driver.py`)

The runtime's execution flow is defined by a `langgraph`. The key components are:

- **`graph.py`**: A module that provides the `build_graph` utility for constructing the main LangGraph.
- **`driver.py`**: A factory for creating the core decision-making component of the graph, which can be either a simple heuristic-based driver or a more powerful LLM-based driver.

### 4. Tool Management (`tools/`, `nodes/tool_node.py`)

The runtime includes a flexible and extensible system for managing tools:

- **`tools/`**: A package that provides utilities for loading tools from the `quadracode-tools` package and from MCP servers.
- **`tool_node.py`**: A module that aggregates all the available tools and creates a unified `ToolNode` for the LangGraph.

### 5. Configuration and State (`config/`, `state.py`)

The runtime's behavior is configured through a set of Pydantic models, and its execution is tracked in a central state object:

- **`config/`**: A package that defines the configuration models for the runtime's subsystems, such as the `ContextEngineConfig`.
- **`state.py`**: A module that defines the `QuadraCodeState` TypedDict, which is the central state object for the LangGraph.

### 6. Messaging and Runtime (`messaging.py`, `runtime.py`)

The runtime communicates with the outside world and other Quadracode components through a Redis streams-based messaging system:

- **`messaging.py`**: A module that provides the core `Mailbox` class for interacting with the Redis streams.
- **`runtime.py`**: A module that provides the `run_forever` function, which is the main entry point for running a Quadracode runtime service.

## Conclusion

The `quadracode-runtime` is a powerful and flexible framework that provides the core foundation for the Quadracode ecosystem. Its modular design, sophisticated context management system, and robust support for autonomous operation make it a state-of-the-art platform for building and deploying advanced AI agents.

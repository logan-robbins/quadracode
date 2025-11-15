# Quadracode Agent Implementation

The `quadracode-agent` is a specialized Python package that defines and configures a LangGraph-based autonomous agent. This agent is built upon the shared `quadracode-runtime`, inheriting its core functionalities while injecting agent-specific behaviors through a customized profile. The primary responsibility of this module is to assemble and expose a runnable agent graph that can be executed as a persistent runtime service.

## Core Components

### 1. Agent Profile (`profile.py`)

The agent's behavior is fundamentally defined by its profile. The `profile.py` module is the central hub for this configuration. It imports a generic `AGENT_PROFILE` from the `quadracode_runtime` and customizes it to suit the agent's specific needs.

- **Profile Customization**: The module uses `dataclasses.replace` to create a new profile instance, swapping the default system prompt with a specialized one. This approach allows the agent to inherit a base set of tools and configurations from the runtime while overriding key behavioral parameters.
- **System Prompt**: The customized profile includes a `SYSTEM_PROMPT` that instructs the agent on its autonomous nature, its control over tool usage, and the rules for interacting with the workspace. This prompt is critical for shaping the agent's decision-making process.

### 2. System Prompt (`prompts/system.py`)

This module contains the `SYSTEM_PROMPT` string, which serves as the foundational instruction set for the agent. The prompt is designed to be technically dense, providing clear directives on:

- **Autonomous Control**: The agent is explicitly told that it has complete control over which tools to call, the sequence of tool calls, and when to conclude its work.
- **Workspace Interaction**: It defines strict rules for filesystem and command execution, mandating the use of the `workspace` toolset and confining all artifacts to the `/workspace` mount path. This ensures that the agent's operations are contained and predictable.

### 3. LangGraph Graph (`graph.py`)

The `graph.py` module is responsible for constructing the agent's primary operational workflow. It leverages the `build_graph` utility from the `quadracode_runtime` to create a runnable LangGraph instance.

- **Graph Initialization**: The `build_graph` function takes the agent's `SYSTEM_PROMPT` as a key parameter, which is used to configure the root node of the graph.
- **Runnable Instance**: The resulting `graph` object is a fully configured LangGraph that executes the agent's logic, including message processing, tool selection, and state management.

### 4. Entry Point (`__main__.py`)

The `__main__.py` module provides the entry point for running the agent as a standalone service. It utilizes the `run_forever` function from the `quadracode_runtime`, which is an asynchronous loop that manages the agent's lifecycle.

- **Asynchronous Execution**: The `main` function uses `asyncio.run` to start the `run_forever` loop, passing in the agent's `PROFILE` to configure the runtime environment.
- **Service Operation**: This allows the agent to operate as a persistent, message-driven service, continuously processing inputs from its mailbox.

## Package Structure (`__init__.py`)

The `__init__.py` file serves as the main entry point for the `quadracode_agent` package. It exposes the customized `PROFILE` object, making it easily importable by other modules and by the `langgraph` CLI for local development and debugging.

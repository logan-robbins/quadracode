# Quadracode Tools: `IMPLEMENTATION.md`

## Overview

The `quadracode-tools` package provides a comprehensive suite of LangChain tools that serve as the primary capabilities for Quadracode agents. These tools are the bridge between an agent's reasoning loop and the external world, enabling actions such as filesystem manipulation, code execution, automated testing, and lifecycle management. The package is designed to be self-contained and extensible, with a clear separation of concerns between different tool categories.

## Core Principles

- **Structured Inputs**: All complex tools use Pydantic models to define their input schemas. This ensures that agents provide well-formed, validated arguments, preventing a wide class of errors and improving the reliability of tool calls.
- **JSON Outputs**: Tools return structured JSON objects as strings. This provides a machine-readable format that agents can easily parse and reason about, enabling them to build robust workflows that chain tool outputs to inputs.
- **Abstraction over Execution Environments**: The tools abstract away the details of the underlying execution environment. For instance, the `workspace_*` tools manage Docker containers and volumes, but the agent interacts with them through a high-level API, making the system more portable.
- **Observability**: Key actions, particularly those related to workspaces and autonomous control, publish events to Redis streams. This creates a detailed audit trail for monitoring, debugging, and analyzing agent behavior.

## Tool Categories and Architecture

The tools are organized into several logical categories, each corresponding to a distinct set of capabilities.

### 1. Filesystem and Execution

These are the most fundamental tools, providing the agent with the ability to read and write files and execute arbitrary code.

- **`read_file`**: Reads the entire content of a UTF-8 text file. Essential for ingesting source code, configuration, and data.
- **`write_file`**: Writes text content to a file, creating parent directories as needed. It overwrites existing files, providing a simple and idempotent way to modify the filesystem.
- **`bash_shell`**: Executes a command in a Bash login shell (`bash -lc`), returning `stdout`, `stderr`, and the `returncode`. This is a powerful, general-purpose tool for a wide range of system interactions.
- **`python_repl`**: Executes a snippet of Python code and returns the resulting local variables as a JSON object. Useful for quick calculations, data transformations, and simple scripting.

### 2. Workspace Management

The workspace tools provide an isolated, persistent environment for each agent task, backed by Docker containers and volumes.

- **`workspace_create`**: Creates or re-attaches to a workspace container and its associated volume. This is an idempotent operation and the entry point for any workspace-related task.
- **`workspace_exec`**: Executes a shell command inside the workspace container. This is the primary way an agent interacts with its sandboxed environment. Command outputs are logged to files within the workspace for auditing.
- **`workspace_copy_to` / `workspace_copy_from`**: Tools for transferring files between the host machine and the workspace volume, providing a bridge for data exchange.
- **`workspace_destroy`**: Stops the container and, optionally, deletes the Docker volume, allowing for resource cleanup.
- **`workspace_info`**: Retrieves metadata and status information about a workspace, including an option to calculate disk usage.

### 3. Agent Lifecycle and Discovery

These tools allow agents, particularly the orchestrator, to manage the pool of available agents and for agents to discover each other.

- **`agent_registry_tool`**: Interacts with the Quadracode Agent Registry REST API. It supports operations like listing agents, registering new agents, sending heartbeats, and managing `hotpath` status.
- **`agent_management_tool`**: Provides a higher-level interface for managing the agent lifecycle. It delegates to shell scripts to abstract the underlying container runtime (e.g., Docker) for actions like spawning and deleting agents.

### 4. Automated Testing and Quality Assurance

A key feature of Quadracode is its emphasis on automated quality checks. These tools enable agents to write and run tests autonomously.

- **`run_full_test_suite`**: Discovers and executes all test commands found in a workspace (e.g., `pytest`, `npm test`). It captures detailed, structured results, including coverage metrics. In case of failure, it can automatically spawn a debugger agent.
- **`generate_property_tests`**: A powerful tool that allows an agent to dynamically generate and execute Hypothesis-driven property-based tests. The agent provides a data generation strategy and a test body, and the tool runs the test in an isolated subprocess.

### 5. Autonomous Control and Meta-Cognition

These tools are designed for agents operating in a fully autonomous mode, allowing them to manage their own long-running tasks and learning processes.

- **`manage_refinement_ledger`**: Interacts with the Plan-Replan-Propose (PRP) ledger, allowing an agent to record hypotheses, conclude their outcomes, and query past failures to inform future strategies.
- **`autonomous_checkpoint`**: Records a milestone in a long-running task, providing observability into the agent's progress.
- **`autonomous_escalate`**: Allows an agent to request human intervention when it encounters a fatal, unrecoverable error.
- **`hypothesis_critique`**: Enables an agent to perform a structured self-critique of its own problem-solving approaches, fostering a learning loop.
- **`request_final_review`**: A quality gate that requires the full test suite to pass before an agent can submit its work for final human approval.

### 6. Client and Assembly

- **`mcp_client`**: A minimal HTTP client for invoking external tools hosted on a Multi-Capability Platform (MCP) server.
- **`assembly.py`**: A central module that collects all the individual tools and provides a single `get_tools()` function for the Quadracode runtime to consume. This simplifies the registration of tools within the LangGraph framework.

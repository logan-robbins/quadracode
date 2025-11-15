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

## Conclusion

The `quadracode-orchestrator` is a sophisticated and highly configurable component that is central to the operation of the Quadracode system. Its ability to dynamically switch between human-supervised and fully autonomous modes, combined with its powerful agent and workspace management capabilities, makes it a flexible and robust solution for coordinating complex, multi-agent workflows.

# Quadracode UI Implementation

The `quadracode-ui` package provides a Streamlit-based graphical user interface for interacting with and monitoring the Quadracode system. It serves as the primary human-in-the-loop interface for sending commands, observing agent behavior, and managing autonomous tasks.

## Core Components

### Streamlit Application (`app.py`)

The main entry point is `app.py`, which launches the Streamlit server. The application is organized into a tabbed interface for different functionalities:

-   **Chat**: The primary interface for sending messages to the Quadracode orchestrator. User inputs are packaged into `MessageEnvelope` objects and sent to the `qc:mailbox:orchestrator` Redis stream.
-   **Streams**: A debugging tool for inspecting raw Redis stream messages in any of the system's mailboxes. This is useful for observing the flow of messages between components.
-   **Context Metrics**: A dashboard that visualizes metrics from the context engineering system, such as context quality scores, token counts, and tool usage.
-   **Autonomous**: A view for monitoring events related to autonomous operations, including checkpoints, critiques, and guardrail triggers.

### State Management

The UI is stateful, using Streamlit's `session_state` to manage chat histories, active sessions, and user settings. A background thread (`_ensure_mailbox_watcher`) listens for incoming messages on the appropriate supervisor mailbox (`qc:mailbox:human` or `qc:mailbox:human_clone`) and triggers a UI rerun to display new messages, providing a near-real-time experience.

## Communication with Quadracode

All communication with the backend is asynchronous via Redis Streams. The UI acts as a producer to the orchestrator's mailbox and a consumer of the human/supervisor's mailbox. This decoupled architecture allows the UI to be developed and deployed independently of the core Quadracode services.

## Workspace Management

The UI includes a sidebar panel for managing per-chat workspaces. These workspaces are isolated environments where agents can perform tasks. The UI provides controls to:

-   **Create and Destroy**: Provision and tear down workspaces.
-   **Copy Files**: Transfer files from the workspace to the local filesystem.
-   **View Logs and Events**: Inspect logs generated within the workspace and monitor a dedicated Redis stream for workspace-specific events.

These actions are performed by invoking tools from the `quadracode-tools` package, which abstract the underlying details of interacting with the workspace infrastructure (e.g., Docker containers).

## Autonomous Mode

The UI supports a `HUMAN_OBSOLETE` mode, which delegates high-level tasks to the autonomous capabilities of the Quadracode system. When enabled, the UI sends messages with an `autonomous` mode flag and associated settings (e.g., max iterations, runtime). The `_active_supervisor` is switched to `human_clone`, directing messages to a different mailbox monitored by autonomous agents. An "Emergency Stop" button is provided to halt autonomous operations.

## Testing

The UI is tested using a combination of unit, integration, and end-to-end tests:

-   **Unit Tests**: Use `streamlit.testing.v1.AppTest` and a `FakeRedis` stub to test UI components and logic in isolation.
-   **Integration Tests**: Run against a live Redis instance to verify message passing.
-   **End-to-End Tests**: Validate the UI against a full, `docker-compose`-deployed Quadracode stack, ensuring the entire system is functioning correctly.

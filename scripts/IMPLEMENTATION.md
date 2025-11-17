# Scripts Implementation

The `scripts/` directory contains a collection of shell scripts for managing and interacting with the Quadracode system. These scripts are designed for both manual use by developers and programmatic invocation by other tools or CI/CD pipelines.

## Agent Management (`agent-management/`)

This subdirectory contains scripts for managing the lifecycle of Quadracode agents on different container platforms (Docker and Kubernetes). The platform is selected via the `AGENT_RUNTIME_PLATFORM` environment variable. All scripts in this directory produce JSON output for easy parsing.

-   **`spawn-agent.sh`**: Deploys a new agent instance. It can auto-generate an agent ID and configures the container/pod with the necessary environment variables and volume mounts.
-   **`list-agents.sh`**: Discovers and lists all running and stopped agent instances.
-   **`get-agent-status.sh`**: Retrieves detailed status information for a specific agent by its ID.
-   **`delete-agent.sh`**: Stops and removes an existing agent instance.
-   **`purge-workspaces.sh`**: A maintenance script that removes stale agent workspace containers and volumes based on age and status.

## Development and Debugging

These scripts are intended to assist with common development and debugging tasks.

-   **`launch_agent.sh`**: A simple wrapper for `docker run` to quickly launch a single, temporary agent for testing. It automatically generates an agent ID.
-   **`tail_streams.sh`**: A powerful debugging tool that provides a real-time, color-coded view of all message traffic across the Quadracode Redis mailboxes. It uses `redis-cli` and `jq` to format the output.
-   **`run_atlassian_mcp.sh`**: Launches the Atlassian Rovo MCP proxy client, which is a necessary component for agents that need to interact with Atlassian products like Jira and Confluence.

## Platform Abstraction

The agent management scripts abstract away the details of the underlying container platform. The logic for Docker and Kubernetes is encapsulated in separate functions within each script (e.g., `spawn_docker` and `spawn_kubernetes`). A `case` statement at the end of each script dispatches to the appropriate function based on the `AGENT_RUNTIME_PLATFORM` variable, allowing the same script to be used in different deployment environments.

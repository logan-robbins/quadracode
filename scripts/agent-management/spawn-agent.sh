#!/usr/bin/env bash
#
# Spawns a new Quadracode agent on either Docker or Kubernetes.
#
# This script handles the lifecycle of an agent instance. It can generate a unique
# agent ID if one is not provided. The script supports two platforms, determined
# by the `AGENT_RUNTIME_PLATFORM` environment variable:
#   - `docker`: Spawns a new Docker container with the agent image. It injects
#     necessary environment variables for configuration (Redis, registry, API keys)
#     and mounts shared volumes.
#   - `kubernetes`: Creates a new Kubernetes Pod from a dynamically generated
#     manifest. It configures the pod with environment variables and mounts
#     PersistentVolumeClaims for shared data.
#
# The script outputs a JSON object indicating success or failure, which is useful
# for programmatic invocation.
#
# Usage:
#   spawn-agent.sh [AGENT_ID] [IMAGE] [NETWORK]
#
# Environment Variables:
#   AGENT_RUNTIME_PLATFORM: Determines the execution platform ('docker' or 'kubernetes').
#                          Defaults to 'docker'.
#   REDIS_HOST, REDIS_PORT: Redis connection details.
#   AGENT_REGISTRY_URL: URL for the agent registry service.
#   ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.: API keys for various services.
#

set -euo pipefail

# Configuration
PLATFORM="${AGENT_RUNTIME_PLATFORM:-docker}"
AGENT_ID="${1:-}"
IMAGE="${2:-quadracode-agent}"
NETWORK="${3:-quadracode_default}"

# Generate agent ID if not provided
if [[ -z "$AGENT_ID" ]]; then
    SUFFIX=$(openssl rand -hex 4 2>/dev/null || head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c 8)
    AGENT_ID="agent-${SUFFIX}"
fi

CONTAINER_NAME="qc-${AGENT_ID}"

# JSON output helper
json_output() {
    # Generates a JSON-formatted string representing the script's result.
    #
    # Args:
    #   $1: Success status ("true" or "false").
    #   $2: A human-readable message.
    #   $3: (Optional) An error message.
    #
    local success="$1"
    local message="$2"
    local error="${3:-}"

    if [[ "$success" == "true" ]]; then
        cat <<EOF
{
  "success": true,
  "agent_id": "${AGENT_ID}",
  "container_name": "${CONTAINER_NAME}",
  "platform": "${PLATFORM}",
  "image": "${IMAGE}",
  "message": "${message}"
}
EOF
    else
        cat <<EOF
{
  "success": false,
  "agent_id": "${AGENT_ID}",
  "platform": "${PLATFORM}",
  "error": "${error}",
  "message": "${message}"
}
EOF
    fi
}

# Docker implementation
spawn_docker() {
    # Spawns an agent as a Docker container.
    #
    # Checks for existing containers, collects environment variables from the
    # host, and mounts the necessary volumes (`quadracode_shared-data`, etc.).
    # The container is started in detached mode and configured to restart
    # unless stopped.
    #
    # Returns 1 if the container already exists or if `docker run` fails.
    #
    # Check if container already exists
    if docker ps -a --filter "name=${CONTAINER_NAME}" --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
        json_output "false" "Container already exists" "Container ${CONTAINER_NAME} already exists"
        return 1
    fi

    # Collect environment variables
    local env_args=(
        "--env" "QUADRACODE_ID=${AGENT_ID}"
        "--env" "REDIS_HOST=${REDIS_HOST:-redis}"
        "--env" "REDIS_PORT=${REDIS_PORT:-6379}"
        "--env" "AGENT_REGISTRY_URL=${AGENT_REGISTRY_URL:-http://agent-registry:8090}"
        "--env" "MCP_REDIS_SERVER_URL=${MCP_REDIS_SERVER_URL:-http://redis-mcp:8000/mcp}"
        "--env" "MCP_REDIS_TRANSPORT=${MCP_REDIS_TRANSPORT:-streamable-http}"
        "--env" "MCP_REMOTE_CACHE_DIR=${MCP_REMOTE_CACHE_DIR:-/var/lib/mcp-remote}"
        "--env" "QUADRACODE_AGENT_AUTOREGISTER=${QUADRACODE_AGENT_AUTOREGISTER:-1}"
        "--env" "QUADRACODE_AGENT_HEARTBEAT_INTERVAL=${QUADRACODE_AGENT_HEARTBEAT_INTERVAL:-15}"
    )

    # Add API keys if present
    [[ -n "${ANTHROPIC_API_KEY:-}" ]] && env_args+=("--env" "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}")
    [[ -n "${OPENAI_API_KEY:-}" ]] && env_args+=("--env" "OPENAI_API_KEY=${OPENAI_API_KEY}")
    [[ -n "${PERPLEXITY_API_KEY:-}" ]] && env_args+=("--env" "PERPLEXITY_API_KEY=${PERPLEXITY_API_KEY}")
    [[ -n "${FIRECRAWL_API_KEY:-}" ]] && env_args+=("--env" "FIRECRAWL_API_KEY=${FIRECRAWL_API_KEY}")

    local shared_path_value="${SHARED_PATH:-/shared}"
    local workspace_mount="${QUADRACODE_WORKSPACE_MOUNT:-/workspace}"
    local volume_args=(
        -v quadracode_shared-data:/shared
        -v quadracode_mcp-remote-cache:/var/lib/mcp-remote
    )

    if [[ -n "${QUADRACODE_WORKSPACE_VOLUME:-}" ]]; then
        shared_path_value="${workspace_mount}"
        env_args+=("--env" "WORKSPACE_MOUNT=${workspace_mount}")
        env_args+=("--env" "WORKSPACE_VOLUME=${QUADRACODE_WORKSPACE_VOLUME}")
        if [[ -n "${QUADRACODE_WORKSPACE_ID:-}" ]]; then
            env_args+=("--env" "WORKSPACE_ID=${QUADRACODE_WORKSPACE_ID}")
        fi
        volume_args+=("-v" "${QUADRACODE_WORKSPACE_VOLUME}:${workspace_mount}")
    fi

    env_args+=("--env" "SHARED_PATH=${shared_path_value}")

    # Spawn container
    local container_id
    if ! container_id=$(docker run -d \
        --name "${CONTAINER_NAME}" \
        --network "${NETWORK}" \
        --restart unless-stopped \
        "${env_args[@]}" \
        "${volume_args[@]}" \
        "${IMAGE}" \
        uv run python -m quadracode_agent 2>&1); then
        json_output "false" "Failed to spawn agent" "${container_id}"
        return 1
    fi

    json_output "true" "Agent ${AGENT_ID} spawned successfully. It will auto-register and begin processing work."
}

# Kubernetes implementation
spawn_kubernetes() {
    # Spawns an agent as a Kubernetes Pod.
    #
    # Checks for an existing Pod with the same name and generates a Pod manifest
    # dynamically. The manifest configures environment variables, with secrets
    # sourced from a `quadracode-secrets` Kubernetes Secret. It also mounts
    # `PersistentVolumeClaim`s for shared data.
    #
    # Returns 1 if the Pod already exists or if `kubectl apply` fails.
    #
    local namespace="${QUADRACODE_NAMESPACE:-default}"

    # Check if pod already exists
    if kubectl get pod "${CONTAINER_NAME}" -n "${namespace}" &>/dev/null; then
        json_output "false" "Pod already exists" "Pod ${CONTAINER_NAME} already exists in namespace ${namespace}"
        return 1
    fi

    # Create pod manifest
    local manifest
    manifest=$(cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${CONTAINER_NAME}
  namespace: ${namespace}
  labels:
    app: quadracode-agent
    agent-id: ${AGENT_ID}
spec:
  restartPolicy: Never
  containers:
  - name: agent
    image: ${IMAGE}
    command: ["uv", "run", "python", "-m", "quadracode_agent"]
    env:
    - name: QUADRACODE_ID
      value: "${AGENT_ID}"
    - name: REDIS_HOST
      value: "${REDIS_HOST:-redis}"
    - name: REDIS_PORT
      value: "${REDIS_PORT:-6379}"
    - name: AGENT_REGISTRY_URL
      value: "${AGENT_REGISTRY_URL:-http://agent-registry:8090}"
    - name: MCP_REDIS_SERVER_URL
      value: "${MCP_REDIS_SERVER_URL:-http://redis-mcp:8000/mcp}"
    - name: MCP_REDIS_TRANSPORT
      value: "${MCP_REDIS_TRANSPORT:-streamable-http}"
    - name: SHARED_PATH
      value: "${SHARED_PATH:-/shared}"
    - name: MCP_REMOTE_CACHE_DIR
      value: "${MCP_REMOTE_CACHE_DIR:-/var/lib/mcp-remote}"
    - name: QUADRACODE_AGENT_AUTOREGISTER
      value: "${QUADRACODE_AGENT_AUTOREGISTER:-1}"
    - name: QUADRACODE_AGENT_HEARTBEAT_INTERVAL
      value: "${QUADRACODE_AGENT_HEARTBEAT_INTERVAL:-15}"
    - name: ANTHROPIC_API_KEY
      valueFrom:
        secretKeyRef:
          name: quadracode-secrets
          key: anthropic-api-key
          optional: true
    - name: OPENAI_API_KEY
      valueFrom:
        secretKeyRef:
          name: quadracode-secrets
          key: openai-api-key
          optional: true
    volumeMounts:
    - name: shared-data
      mountPath: /shared
    - name: mcp-cache
      mountPath: /var/lib/mcp-remote
  volumes:
  - name: shared-data
    persistentVolumeClaim:
      claimName: quadracode-shared-data
  - name: mcp-cache
    persistentVolumeClaim:
      claimName: quadracode-mcp-cache
EOF
)

    # Apply manifest
    if ! echo "${manifest}" | kubectl apply -f - 2>&1; then
        json_output "false" "Failed to spawn agent pod" "kubectl apply failed"
        return 1
    fi

    json_output "true" "Agent ${AGENT_ID} pod created in namespace ${namespace}. It will auto-register and begin processing work."
}

# Main execution
case "$PLATFORM" in
    docker)
        spawn_docker
        ;;
    kubernetes|k8s)
        spawn_kubernetes
        ;;
    *)
        json_output "false" "Unsupported platform" "AGENT_RUNTIME_PLATFORM must be 'docker' or 'kubernetes', got '${PLATFORM}'"
        exit 1
        ;;
esac

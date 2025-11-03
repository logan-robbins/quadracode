#!/usr/bin/env bash
# Delete a Quadracode agent
# Usage: delete-agent.sh AGENT_ID
# Environment: AGENT_RUNTIME_PLATFORM (docker|kubernetes), defaults to docker

set -euo pipefail

# Configuration
PLATFORM="${AGENT_RUNTIME_PLATFORM:-docker}"
AGENT_ID="${1:?Agent ID is required}"
CONTAINER_NAME="qc-${AGENT_ID}"

# JSON output helper
json_output() {
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
delete_docker() {
    # Check if container exists
    if ! docker ps -a --filter "name=${CONTAINER_NAME}" --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
        json_output "false" "Container not found" "Container ${CONTAINER_NAME} does not exist"
        return 1
    fi

    # Stop container
    local stop_output
    if ! stop_output=$(docker stop "${CONTAINER_NAME}" 2>&1); then
        json_output "false" "Failed to stop container" "${stop_output}"
        return 1
    fi

    # Remove container
    local rm_output
    if ! rm_output=$(docker rm "${CONTAINER_NAME}" 2>&1); then
        json_output "false" "Failed to remove container" "${rm_output}"
        return 1
    fi

    json_output "true" "Agent ${AGENT_ID} stopped and removed successfully. It will be automatically unregistered from the registry."
}

# Kubernetes implementation
delete_kubernetes() {
    local namespace="${QUADRACODE_NAMESPACE:-default}"

    # Check if pod exists
    if ! kubectl get pod "${CONTAINER_NAME}" -n "${namespace}" &>/dev/null; then
        json_output "false" "Pod not found" "Pod ${CONTAINER_NAME} does not exist in namespace ${namespace}"
        return 1
    fi

    # Delete pod
    local delete_output
    if ! delete_output=$(kubectl delete pod "${CONTAINER_NAME}" -n "${namespace}" 2>&1); then
        json_output "false" "Failed to delete pod" "${delete_output}"
        return 1
    fi

    json_output "true" "Agent ${AGENT_ID} pod deleted from namespace ${namespace}. It will be automatically unregistered from the registry."
}

# Main execution
case "$PLATFORM" in
    docker)
        delete_docker
        ;;
    kubernetes|k8s)
        delete_kubernetes
        ;;
    *)
        json_output "false" "Unsupported platform" "AGENT_RUNTIME_PLATFORM must be 'docker' or 'kubernetes', got '${PLATFORM}'"
        exit 1
        ;;
esac

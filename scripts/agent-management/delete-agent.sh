#!/usr/bin/env bash
#
# Deletes a Quadracode agent from Docker or Kubernetes.
#
# This script terminates and removes an agent instance identified by its ID.
# It supports two platforms, determined by the `AGENT_RUNTIME_PLATFORM` env var:
#   - `docker`: Stops and removes the agent's Docker container.
#   - `kubernetes`: Deletes the agent's Kubernetes Pod.
#
# The script provides JSON output indicating the outcome of the operation.
#
# Usage:
#   delete-agent.sh AGENT_ID
#
# Environment Variables:
#   AGENT_RUNTIME_PLATFORM: The container platform ('docker' or 'kubernetes').
#                          Defaults to 'docker'.
#

set -euo pipefail

# Configuration
PLATFORM="${AGENT_RUNTIME_PLATFORM:-docker}"
AGENT_ID="${1:?Agent ID is required}"
CONTAINER_NAME="qc-${AGENT_ID}"

# JSON output helper
json_output() {
    # Generates a JSON-formatted string indicating the result of the delete operation.
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
    # Deletes an agent container from Docker.
    #
    # First checks if the container exists, then stops and removes it.
    # Returns 1 if the container is not found or if any Docker command fails.
    #
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
    # Deletes an agent Pod from Kubernetes.
    #
    # Checks if the Pod exists in the specified namespace and then deletes it
    # using `kubectl delete pod`.
    # Returns 1 if the Pod is not found or if the delete command fails.
    #
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

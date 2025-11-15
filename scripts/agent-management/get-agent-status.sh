#!/usr/bin/env bash
#
# Retrieves the status of a specific Quadracode agent from Docker or Kubernetes.
#
# This script inspects the runtime state of an agent given its ID. It operates
# against either a Docker daemon or a Kubernetes cluster, determined by the
# `AGENT_RUNTIME_PLATFORM` environment variable.
#   - `docker`: Uses `docker inspect` to retrieve detailed information about the
#     agent's container, including its status, running state, and image.
#   - `kubernetes`: Uses `kubectl get pod -o json` to fetch the full Pod spec
#     and status, providing details on phase, conditions, and IP.
#
# The output is a JSON object containing the status details, suitable for
# programmatic consumption.
#
# Usage:
#   get-agent-status.sh AGENT_ID
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
    # Generates a JSON-formatted string with the agent's status.
    #
    # Args:
    #   $1: Success status ("true" or "false").
    #   $2: A human-readable message.
    #   $3: (Optional) A JSON string containing the status details. Defaults to {}.
    #   $4: (Optional) An error message.
    #
    local success="$1"
    local message="$2"
    local status_json="${3:-{}}"
    local error="${4:-}"

    if [[ "$success" == "true" ]]; then
        cat <<EOF
{
  "success": true,
  "agent_id": "${AGENT_ID}",
  "platform": "${PLATFORM}",
  "status": ${status_json},
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
status_docker() {
    # Retrieves the status of an agent container from Docker.
    #
    # Uses `docker inspect` to get the container's state and extracts key
    # fields like status, running state, and exit code into a JSON object.
    # Returns 1 if the container is not found or if inspection fails.
    #
    # Check if container exists
    if ! docker ps -a --filter "name=${CONTAINER_NAME}" --format "{{.Names}}" | grep -q "^${CONTAINER_NAME}$"; then
        json_output "false" "Container not found" "{}" "Container ${CONTAINER_NAME} does not exist"
        return 1
    fi

    # Get container inspection data
    local inspect_output
    if ! inspect_output=$(docker inspect "${CONTAINER_NAME}" 2>&1); then
        json_output "false" "Failed to inspect container" "{}" "${inspect_output}"
        return 1
    fi

    # Extract relevant fields
    local status_json
    status_json=$(echo "${inspect_output}" | jq -c '.[0] | {
        container_id: .Id[0:12],
        container_name: .Name,
        status: .State.Status,
        running: .State.Running,
        started_at: .State.StartedAt,
        finished_at: .State.FinishedAt,
        exit_code: .State.ExitCode,
        image: .Config.Image,
        created: .Created
    }')

    json_output "true" "Container status retrieved successfully" "${status_json}"
}

# Kubernetes implementation
status_kubernetes() {
    # Retrieves the status of an agent Pod from Kubernetes.
    #
    # Uses `kubectl get pod -o json` to fetch the Pod's details and extracts
    # relevant status fields like phase, conditions, and IP address.
    # Returns 1 if the Pod is not found or if the kubectl command fails.
    #
    local namespace="${QUADRACODE_NAMESPACE:-default}"

    # Check if pod exists
    if ! kubectl get pod "${CONTAINER_NAME}" -n "${namespace}" &>/dev/null; then
        json_output "false" "Pod not found" "{}" "Pod ${CONTAINER_NAME} does not exist in namespace ${namespace}"
        return 1
    fi

    # Get pod details
    local pod_output
    if ! pod_output=$(kubectl get pod "${CONTAINER_NAME}" -n "${namespace}" -o json 2>&1); then
        json_output "false" "Failed to get pod status" "{}" "${pod_output}"
        return 1
    fi

    # Extract relevant fields
    local status_json
    status_json=$(echo "${pod_output}" | jq -c '{
        pod_name: .metadata.name,
        namespace: .metadata.namespace,
        status: .status.phase,
        conditions: .status.conditions,
        container_statuses: .status.containerStatuses,
        started_at: .status.startTime,
        pod_ip: .status.podIP,
        node_name: .spec.nodeName,
        created: .metadata.creationTimestamp
    }')

    json_output "true" "Pod status retrieved successfully" "${status_json}"
}

# Main execution
case "$PLATFORM" in
    docker)
        status_docker
        ;;
    kubernetes|k8s)
        status_kubernetes
        ;;
    *)
        json_output "false" "Unsupported platform" "{}" "AGENT_RUNTIME_PLATFORM must be 'docker' or 'kubernetes', got '${PLATFORM}'"
        exit 1
        ;;
esac

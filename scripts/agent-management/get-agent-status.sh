#!/usr/bin/env bash
# Get status of a specific Quadracode agent
# Usage: get-agent-status.sh AGENT_ID
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

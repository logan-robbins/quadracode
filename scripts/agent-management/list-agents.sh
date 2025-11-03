#!/usr/bin/env bash
# List Quadracode agent containers/pods
# Usage: list-agents.sh
# Environment: AGENT_RUNTIME_PLATFORM (docker|kubernetes), defaults to docker

set -euo pipefail

# Configuration
PLATFORM="${AGENT_RUNTIME_PLATFORM:-docker}"

# JSON output helper
json_output() {
    local success="$1"
    local message="$2"
    local containers_json="$3"
    local error="${4:-}"

    if [[ "$success" == "true" ]]; then
        cat <<EOF
{
  "success": true,
  "platform": "${PLATFORM}",
  "containers": ${containers_json},
  "count": $(echo "${containers_json}" | jq 'length'),
  "message": "${message}"
}
EOF
    else
        cat <<EOF
{
  "success": false,
  "platform": "${PLATFORM}",
  "error": "${error}",
  "message": "${message}"
}
EOF
    fi
}

# Docker implementation
list_docker() {
    local output
    if ! output=$(docker ps -a --filter "name=qc-agent-" --format "{{.Names}}\t{{.Status}}\t{{.ID}}" 2>&1); then
        json_output "false" "Failed to list containers" "[]" "${output}"
        return 1
    fi

    if [[ -z "$output" ]]; then
        json_output "true" "No agent containers found" "[]"
        return 0
    fi

    # Build JSON array
    local containers=()
    while IFS=$'\t' read -r name status container_id; do
        [[ -z "$name" ]] && continue
        # Extract agent_id from container name (qc-agent-xxx -> agent-xxx)
        local agent_id="${name#qc-}"

        containers+=("{\"agent_id\":\"${agent_id}\",\"container_name\":\"${name}\",\"container_id\":\"${container_id:0:12}\",\"status\":\"${status}\"}")
    done <<< "$output"

    local containers_json="[$(IFS=,; echo "${containers[*]}")]"
    json_output "true" "Found ${#containers[@]} agent container(s)" "${containers_json}"
}

# Kubernetes implementation
list_kubernetes() {
    local namespace="${QUADRACODE_NAMESPACE:-default}"
    local output

    if ! output=$(kubectl get pods -n "${namespace}" -l app=quadracode-agent -o json 2>&1); then
        json_output "false" "Failed to list pods" "[]" "${output}"
        return 1
    fi

    # Parse JSON output
    local containers
    containers=$(echo "${output}" | jq -c '[.items[] | {
        agent_id: .metadata.labels["agent-id"],
        container_name: .metadata.name,
        pod_name: .metadata.name,
        status: .status.phase,
        namespace: .metadata.namespace,
        created: .metadata.creationTimestamp
    }]')

    local count=$(echo "${containers}" | jq 'length')
    json_output "true" "Found ${count} agent pod(s) in namespace ${namespace}" "${containers}"
}

# Main execution
case "$PLATFORM" in
    docker)
        list_docker
        ;;
    kubernetes|k8s)
        list_kubernetes
        ;;
    *)
        echo '{"success":false,"error":"Unsupported platform","message":"AGENT_RUNTIME_PLATFORM must be '\''docker'\'' or '\''kubernetes'\'', got '\'''"${PLATFORM}"''\''"}' >&2
        exit 1
        ;;
esac

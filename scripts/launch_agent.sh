#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME=${1:-quadracode-agent}
shift || true

if [[ -z "${AGENT_ID:-}" ]]; then
  SUFFIX=$(LC_CTYPE=C tr -dc '0-9' </dev/urandom | head -c 8)
  AGENT_ID="agent-${SUFFIX}"
fi

echo "Launching ${IMAGE_NAME} with QUADRACODE_ID=${AGENT_ID}" >&2

exec docker run \
  --rm \
  --env QUADRACODE_ID="${AGENT_ID}" \
  "$IMAGE_NAME" "$@"

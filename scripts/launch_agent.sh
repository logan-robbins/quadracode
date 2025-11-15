#!/usr/bin/env bash
#
# Launches a single Quadracode agent container with a generated ID.
#
# This script is a lightweight wrapper around `docker run` for quickly launching
# a transient agent instance. It automatically generates a unique `AGENT_ID` if
# one is not already present in the environment and injects it into the container
# as the `QUADRACODE_ID` environment variable. The container is run with `--rm`
# so it is automatically cleaned up on exit.
#
# This is useful for development and testing scenarios where a temporary,
# uniquely identified agent is needed without manual ID management.
#
# Usage:
#   scripts/launch_agent.sh [IMAGE_NAME] [DOCKER_RUN_ARGS...]
#
# Args:
#   IMAGE_NAME: The name of the agent Docker image to run.
#               Defaults to 'quadracode-agent'.
#   DOCKER_RUN_ARGS: Additional arguments to pass to `docker run`.
#
# Environment Variables:
#   AGENT_ID: If set, this value is used as the agent's ID instead of
#             generating a new one.
#

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

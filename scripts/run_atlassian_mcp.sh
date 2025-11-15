#!/usr/bin/env bash
#
# Launches the Atlassian Rovo MCP (Managed Compute Platform) proxy client.
#
# This script uses `npx` to run the `mcp-remote` package, which establishes a
# secure connection to the Atlassian MCP SSE (Server-Sent Events) endpoint.
# It configures a local cache directory for MCP resources, which defaults to
# `$HOME/.cache/mcp-remote` but can be overridden by the `MCP_REMOTE_CACHE_DIR`
# environment variable.
#
# This is a prerequisite for agents that need to access Atlassian-hosted
# resources like Jira or Confluence through the MCP infrastructure.
#
# Requires Node.js and npx to be installed.
#
# Usage:
#   scripts/run_atlassian_mcp.sh
#

set -euo pipefail

URL="https://mcp.atlassian.com/v1/sse"
PACKAGE="mcp-remote"
CACHE_DIR=${MCP_REMOTE_CACHE_DIR:-"$HOME/.cache/mcp-remote"}

mkdir -p "$CACHE_DIR"

if command -v npx >/dev/null 2>&1; then
  echo "Launching Atlassian Rovo MCP proxy via npx..." >&2
  echo "Cache directory: $CACHE_DIR" >&2
  exec npx -y "$PACKAGE" --cache-dir "$CACHE_DIR" "$URL"
else
  echo "Error: npx not found. Install Node.js v18+ to use Atlassian MCP (see Atlassian docs)." >&2
  exit 1
fi

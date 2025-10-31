#!/usr/bin/env bash
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

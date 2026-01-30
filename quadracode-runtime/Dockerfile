# ./quadracode-runtime/Dockerfile
# Standalone Dockerfile for Quadracode Runtime
# 
# Supports two modes:
# 1. Production: Requires external Redis and MCP services
# 2. Mock Mode: Uses in-memory Redis mock, can run without external dependencies
#
# BUILD FROM ROOT: docker build -f quadracode-runtime/Dockerfile -t quadracode-runtime .
#
# RUN (Mock Mode - for testing):
#   docker run -e QUADRACODE_MOCK_MODE=true -p 8080:8080 quadracode-runtime
#
# RUN (Production):
#   docker run --network quadracode_default \
#     -e REDIS_HOST=redis \
#     -e MCP_REDIS_SERVER_URL=http://redis-mcp:8000/mcp/ \
#     quadracode-runtime

FROM arm64v8/python:3.12-slim

LABEL maintainer="Quadracode Team"
LABEL description="Quadracode Runtime - Shared runtime core for orchestrator and agents"
LABEL version="0.1.0"

# Standard Quadracode Python service environment
ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker \
    # Default to production mode (set QUADRACODE_MOCK_MODE=true for testing)
    QUADRACODE_MOCK_MODE="" \
    # Health check server port
    QUADRACODE_HEALTH_PORT=8080

# Install UV (fast Python package manager) and required system tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# Stage sibling packages for editable path installs
WORKDIR /app

# Copy dependency packages first (for better layer caching)
COPY quadracode-contracts/ quadracode-contracts/
COPY quadracode-tools/ quadracode-tools/

# Install dependency packages
RUN cd quadracode-contracts && uv pip install --system -e . && cd /app

# Copy runtime package
COPY quadracode-runtime/ quadracode-runtime/

# Install runtime package with all dependencies
RUN cd quadracode-runtime && uv pip install --system -e . && cd /app

# Create a simple health check server script
RUN cat > /app/healthcheck.py << 'EOF'
#!/usr/bin/env python3
"""Simple HTTP health check server for Quadracode Runtime."""
import http.server
import socketserver
import os
import json
from datetime import datetime, timezone

PORT = int(os.environ.get("QUADRACODE_HEALTH_PORT", "8080"))

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/health", "/healthz", "/ok"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {
                "status": "healthy",
                "service": "quadracode-runtime",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mock_mode": os.environ.get("QUADRACODE_MOCK_MODE", "").lower() in ("1", "true", "yes", "on"),
            }
            self.wfile.write(json.dumps(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        pass  # Suppress access logs

if __name__ == "__main__":
    with socketserver.TCPServer(("", PORT), HealthHandler) as httpd:
        print(f"Health check server running on port {PORT}")
        httpd.serve_forever()
EOF

# Create an entrypoint script that starts health check and runtime
RUN cat > /app/entrypoint.sh << 'EOF'
#!/bin/bash
set -e

# Start health check server in background
python /app/healthcheck.py &

# Check if we're in mock mode
if [ "${QUADRACODE_MOCK_MODE,,}" = "true" ] || [ "${QUADRACODE_MOCK_MODE}" = "1" ]; then
    echo "Starting Quadracode Runtime in MOCK MODE..."
    echo "  - Using in-memory Redis mock (fakeredis)"
    echo "  - Using MemorySaver checkpointer"
    echo "  - No external dependencies required"
else
    echo "Starting Quadracode Runtime in PRODUCTION MODE..."
    echo "  - Requires external Redis and MCP services"
fi

# Run the runtime module
exec python -m quadracode_runtime "$@"
EOF
RUN chmod +x /app/entrypoint.sh

# Expose health check port
EXPOSE 8080

# Health check using the HTTP server
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8080/health >/dev/null || exit 1

# Set working directory
WORKDIR /app/quadracode-runtime

# Default command runs the entrypoint script
ENTRYPOINT ["/app/entrypoint.sh"]

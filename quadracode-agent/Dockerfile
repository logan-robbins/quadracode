# ./quadracode-agent/Dockerfile
# Dockerfile for Quadracode Agent
# BUILD FROM ROOT: docker build -f quadracode-agent/Dockerfile -t quadracode-agent .

FROM arm64v8/python:3.12-slim

# Standard Quadracode Python service environment
ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker \
    PYTHONPATH="/app/quadracode-orchestrator:${PYTHONPATH}"

# Mock mode environment (set to "true" for standalone testing without Redis/LLM)
ENV QUADRACODE_MOCK_MODE="false"

# Install UV (fast Python package manager) and Node.js
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && npm install -g corepack \
    && rm -rf /var/lib/apt/lists/*

# Stage sibling packages for editable path installs
WORKDIR /app

COPY quadracode-contracts/ quadracode-contracts/
COPY quadracode-tools/ quadracode-tools/
COPY quadracode-runtime/ quadracode-runtime/
COPY quadracode-orchestrator/ quadracode-orchestrator/

RUN cd quadracode-contracts && uv pip install --system -e . && \
    cd /app/quadracode-runtime && uv pip install --system -e . && \
    cd /app/quadracode-orchestrator && uv pip install --system -e .

# Prepare agent project workspace (../quadracode-tools references stay valid)
WORKDIR /app/quadracode-agent

COPY quadracode-agent/pyproject.toml ./pyproject.toml
COPY quadracode-agent/uv.lock ./uv.lock
COPY quadracode-agent/langgraph-docker.json ./langgraph-docker.json
COPY quadracode-agent/src/quadracode_agent ./quadracode_agent

# Install agent dependencies and project in the uv-managed environment
RUN uv sync --frozen --no-dev || uv pip install --system -e .

# Expose dev server port used by langgraph CLI
EXPOSE 8123

# Health check - supports both LangGraph dev mode and mock mode
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD if [ "$QUADRACODE_MOCK_MODE" = "true" ]; then \
            python -c "import quadracode_agent; print('healthy')" || exit 1; \
        else \
            curl -fsS http://localhost:8123/ok >/dev/null || exit 1; \
        fi

# Entrypoint script to support different modes
COPY --chmod=755 <<'EOF' /app/quadracode-agent/entrypoint.sh
#!/bin/bash
set -e

if [ "$QUADRACODE_MOCK_MODE" = "true" ]; then
    echo "Starting agent in MOCK MODE (no Redis/LLM required)"
    exec python -m quadracode_agent
else
    echo "Starting agent in LangGraph dev mode"
    exec uv run langgraph dev --config langgraph-docker.json --host 0.0.0.0 --port 8123
fi
EOF

# Run via entrypoint for mode-aware startup
CMD ["/app/quadracode-agent/entrypoint.sh"]

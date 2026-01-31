# ./quadracode-orchestrator/Dockerfile
# Dockerfile for Quadracode Orchestrator
# BUILD FROM ROOT: docker build -f quadracode-orchestrator/Dockerfile -t quadracode-orchestrator .
#
# MOCK MODE: Set QUADRACODE_MOCK_MODE=true for standalone testing without Redis/LLM
#   docker run -e QUADRACODE_MOCK_MODE=true quadracode-orchestrator

# Use multi-arch Python 3.12 slim image
FROM python:3.12-slim

# Standard Quadracode Python service environment
ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker \
    # Default mock mode to false - set to "true" for standalone testing
    QUADRACODE_MOCK_MODE=false

# Install UV (fast Python package manager), Docker CLI, and health check tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    lsb-release \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && npm install -g corepack \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Stage sibling packages for editable path installs
WORKDIR /app

COPY quadracode-contracts/ quadracode-contracts/
COPY quadracode-tools/ quadracode-tools/
COPY quadracode-runtime/ quadracode-runtime/

RUN cd quadracode-contracts && uv pip install --system -e . && \
    cd /app/quadracode-runtime && uv pip install --system -e .

# Prepare orchestrator project workspace (../quadracode-tools references stay valid)
WORKDIR /app/quadracode-orchestrator

COPY quadracode-orchestrator/pyproject.toml ./pyproject.toml
COPY quadracode-orchestrator/uv.lock ./uv.lock
COPY quadracode-orchestrator/langgraph-docker.json ./langgraph-docker.json
COPY quadracode-orchestrator/src/quadracode_orchestrator ./quadracode_orchestrator

# Install orchestrator dependencies and project in the uv-managed environment
RUN uv sync --frozen --no-dev || uv pip install --system -e .

# Expose dev server port used by langgraph CLI
EXPOSE 8123

# Health check script that works in both normal and mock mode
# In mock mode, we verify the process is running; in normal mode, verify HTTP endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD if [ "$QUADRACODE_MOCK_MODE" = "true" ]; then \
            pgrep -f "langgraph" > /dev/null || exit 1; \
        else \
            curl -fsS http://localhost:8123/ok >/dev/null || exit 1; \
        fi

# Run the orchestrator LangGraph in dev server mode
# In mock mode, the runtime uses in-memory stores and mock responses
CMD ["uv", "run", "langgraph", "dev", "--config", "langgraph-docker.json", "--host", "0.0.0.0", "--port", "8123"]

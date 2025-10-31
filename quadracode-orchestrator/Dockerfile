# ./quadracode-orchestrator/Dockerfile
# Dockerfile for Quadracode Orchestrator
# BUILD FROM ROOT: docker build -f quadracode-orchestrator/Dockerfile -t quadracode-orchestrator .

FROM arm64v8/python:3.12-slim

# Standard Quadracode Python service environment
ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker

# Install UV (fast Python package manager)
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

RUN cd quadracode-contracts && uv pip install --system -e . && \
    cd /app/quadracode-runtime && uv pip install --system -e .

# Prepare orchestrator project workspace (../quadracode-tools references stay valid)
WORKDIR /app/quadracode-orchestrator

COPY quadracode-orchestrator/pyproject.toml ./pyproject.toml
COPY quadracode-orchestrator/uv.lock ./uv.lock
COPY quadracode-orchestrator/langgraph-docker.json ./langgraph-docker.json
COPY .env ./.env
COPY quadracode-orchestrator/src/quadracode_orchestrator ./quadracode_orchestrator

# Install orchestrator dependencies and project in the uv-managed environment
RUN uv sync --frozen --no-dev || uv pip install --system -e .

# Expose dev server port used by langgraph CLI
EXPOSE 8123

# Health check to verify the LangGraph dev server is up
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8123/ok >/dev/null || exit 1

# Run the orchestrator LangGraph in dev server mode
CMD ["uv", "run", "langgraph", "dev", "--config", "langgraph-docker.json", "--host", "0.0.0.0", "--port", "8123"]

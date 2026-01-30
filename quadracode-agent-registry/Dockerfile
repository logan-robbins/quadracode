# ./quadracode-agent-registry/Dockerfile
# Dockerfile for Quadracode Agent Registry
# BUILD FROM ROOT: docker build -f quadracode-agent-registry/Dockerfile -t quadracode-agent-registry .
# STANDALONE:      docker build -f Dockerfile -t quadracode-agent-registry . (from this directory)

FROM python:3.12-slim

# Standard Quadracode Python service environment
ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker

# Mock mode: set to "true" to use in-memory SQLite (no external dependencies)
ENV QUADRACODE_MOCK_MODE=false

# Install UV (fast Python package manager) and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy agent registry project files first (for better layer caching)
# When built from root context via docker-compose (context: .)
COPY quadracode-agent-registry/pyproject.toml ./
COPY quadracode-agent-registry/src ./src

# Install dependencies using uv
RUN uv pip install --system -e .

# Expose port for registry service
EXPOSE 8090

# Health check for service monitoring (curl is retained for this)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

# Run the agent registry with uvicorn directly
CMD ["python", "-m", "uvicorn", "agent_registry.app:app", "--host", "0.0.0.0", "--port", "8090"]

# ./quadracode-agent-registry/Dockerfile
# Dockerfile for Quadracode Agent Registry
# BUILD FROM ROOT: docker build -f quadracode-agent-registry/Dockerfile -t quadracode-agent-registry .

FROM arm64v8/python:3.12-slim

# Standard Quadracode Python service environment
ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker

# Install UV (fast Python package manager) with cleanup
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install shared package and tools FIRST (standard pattern)
COPY quadracode-contracts/ /app/quadracode-contracts/
COPY quadracode-tools/ /app/quadracode-tools/
RUN cd /app/quadracode-contracts && uv pip install --system -e .

# Copy agent registry project files and source code
COPY quadracode-agent-registry/pyproject.toml ./
COPY quadracode-agent-registry/src ./src
COPY .env ./

# Install agent registry dependencies and package
RUN uv sync --frozen --no-dev || uv pip install --system -e .

# Expose port for registry service (standard pattern)
EXPOSE 8090

# Health check for service monitoring
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

# Run the agent registry
CMD ["uv", "run", "quadracode-agent-registry"]

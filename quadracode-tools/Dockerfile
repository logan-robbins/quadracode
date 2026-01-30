# ./quadracode-tools/Dockerfile
# Dockerfile for Quadracode Tools library
# BUILD FROM ROOT: docker build -f quadracode-tools/Dockerfile -t quadracode-tools .

FROM python:3.12-slim

# Standard Quadracode Python environment
ENV PATH="/root/.cargo/bin:/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker

# Mock mode environment (set to "true" for standalone testing without external deps)
ENV QUADRACODE_MOCK_MODE="false"

# Install UV (fast Python package manager) and Docker CLI for workspace tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    docker.io \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy contracts dependency first (tools depends on it)
COPY quadracode-contracts/ quadracode-contracts/

# Copy tools package
COPY quadracode-tools/ quadracode-tools/

# Install contracts then tools
RUN cd quadracode-contracts && uv pip install --system -e . && \
    cd /app/quadracode-tools && uv pip install --system -e .

# Set working directory to tools
WORKDIR /app/quadracode-tools

# Install test dependencies for running tests
RUN uv pip install --system pytest pytest-mock

# Health check verifies the package imports correctly
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "from quadracode_tools import get_tools; tools = get_tools(); print(f'{len(tools)} tools loaded')" || exit 1

# Default command runs tests
CMD ["python", "-m", "pytest", "tests/", "-v"]

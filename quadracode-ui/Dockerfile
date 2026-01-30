# ./quadracode-ui/Dockerfile
# Dockerfile for Quadracode Streamlit UI
# BUILD FROM ROOT: docker build -f quadracode-ui/Dockerfile -t quadracode-ui .
#
# MOCK MODE:
# docker run -e QUADRACODE_MOCK_MODE=true -p 8501:8501 quadracode-ui
# - No Redis or agent-registry required in mock mode
# - Uses fakeredis for in-memory Redis mock
# - Provides mock data for UI testing and development

FROM python:3.12-slim

ENV PATH="/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker

# Install system dependencies and uv
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy sibling packages required for editable installs
COPY quadracode-contracts/ quadracode-contracts/
COPY quadracode-tools/ quadracode-tools/

# Install contracts and tools packages
RUN cd quadracode-contracts && uv pip install --system -e .
RUN cd quadracode-tools && uv pip install --system -e .

# Prepare UI workspace
RUN mkdir -p /app/quadracode-ui
WORKDIR /app/quadracode-ui

COPY quadracode-ui/pyproject.toml ./pyproject.toml
COPY quadracode-ui/src ./src

# Install project dependencies (skip lock file if not present)
RUN uv pip install --system -e .

# Environment variables with defaults
ENV REDIS_HOST="redis" \
    REDIS_PORT="6379" \
    AGENT_REGISTRY_URL="http://agent-registry:8090" \
    QUADRACODE_MOCK_MODE="false"

EXPOSE 8501

# Health check for standalone operation
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["uv", "run", "streamlit", "run", "src/quadracode_ui/app.py", \
     "--server.address", "0.0.0.0", \
     "--server.port", "8501", \
     "--server.headless", "true"]

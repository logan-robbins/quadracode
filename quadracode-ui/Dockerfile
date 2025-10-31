# ./quadracode-ui/Dockerfile
# Dockerfile for Quadracode Streamlit UI
# BUILD FROM ROOT: docker build -f quadracode-ui/Dockerfile -t quadracode-ui .

FROM arm64v8/python:3.12-slim

ENV PATH="/root/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_ENVIRONMENT=docker

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy sibling package required for editable installs
COPY quadracode-contracts/ quadracode-contracts/
RUN cd quadracode-contracts && uv pip install --system -e .

# Prepare UI workspace
RUN mkdir -p /app/quadracode-ui
WORKDIR /app/quadracode-ui

COPY quadracode-ui/pyproject.toml ./pyproject.toml
COPY quadracode-ui/uv.lock ./uv.lock
COPY quadracode-ui/src ./src

# Install project dependencies
RUN uv sync --frozen --no-dev || uv pip install --system -e .

EXPOSE 8501

CMD ["uv", "run", "streamlit", "run", "src/quadracode_ui/app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]

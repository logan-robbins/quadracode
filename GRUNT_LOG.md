# Grunt Verification Log

## [2026-01-30] quadracode-runtime

- **MAJOR**: Upgraded LangGraph from 0.6.x to 1.0.x (production-ready release)
- **MAJOR**: Upgraded LangChain ecosystem to 1.x series (langchain>=1.0, langchain-anthropic>=1.0, langchain-openai>=1.0)
- **MAJOR**: Updated langgraph-checkpoint-sqlite from 2.x to 3.x (required for LangGraph 1.0)
- Added `QUADRACODE_MOCK_MODE` env var for standalone testing without external dependencies
- Created `/quadracode-runtime/src/quadracode_runtime/mock.py` with fakeredis support
- Created standalone `/quadracode-runtime/Dockerfile` with health check endpoint on port 8080
- Updated `__main__.py` to support mock mode initialization
- Updated `runtime.py`, `observability.py`, `graph.py` to detect and use mock mode
- Added `fakeredis>=2.20` and `httpx>=0.27` dependencies

## [2026-01-30] quadracode-agent-registry

- Added explicit dependency versions: fastapi>=0.115.0, uvicorn[standard]>=0.30.0, pydantic>=2.10.0, pydantic-settings>=2.6.0
- Changed build backend from setuptools to hatchling for modern Python packaging
- Added `QUADRACODE_MOCK_MODE` env var support for standalone testing with in-memory SQLite
- Fixed Dockerfile: removed arm64v8 hardcoding, kept curl for healthcheck, added mock mode support
- Fixed `AgentHeartbeat.agent_id` schema to be optional (populated from URL path)
- Added shared in-memory SQLite connection support for mock mode (prevents connection isolation issues)

## [2026-01-30] quadracode-tools

- Updated dependency versions: langchain-core>=0.3,<0.4, httpx>=0.27,<0.29, redis>=5.0,<8.0, pydantic>=2.0,<3.0
- Fixed Pydantic V2 deprecations: `root_validator` → `model_validator` in agent_management.py and agent_registry.py
- Fixed Pydantic V2 deprecations: `@validator` → `@field_validator` in workspace.py
- Created proper Dockerfile for tools package (Python 3.12, uv, Docker CLI for workspace tools)
- Added local tests/conftest.py to allow tests to run independently without quadracode_runtime dependency

## [2026-01-30] quadracode-ui

- Updated pyproject.toml dependency versions with upper bounds: streamlit>=1.40,<2.0, redis>=5.0,<6.0, httpx>=0.27,<0.28, pandas>=2.0,<3.0, pygments>=2.17,<3.0, plotly>=5.18,<6.0
- Added fakeredis>=2.20,<3.0 and pyyaml>=6.0,<7.0 dependencies
- Updated Dockerfile: removed arm64v8 hardcoding, added healthcheck, added QUADRACODE_MOCK_MODE support
- Added `QUADRACODE_MOCK_MODE` env var support for standalone testing without Redis/agent-registry
- Added `_bool_env()` helper and `MOCK_MODE` config variable in config.py
- Updated redis_client.py: uses fakeredis in mock mode, seeds sample data for demonstration
- Updated Dashboard page: mock agent registry data for agents tab visualization
- Added mock mode indicators to all pages (Chat, Mailbox Monitor, Workspaces, Dashboard, Prompt Settings)
- Workspace operations warn about Docker requirement in mock mode

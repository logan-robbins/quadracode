# Quadracode Agent Registry Implementation

The `quadracode-agent-registry` is a lightweight, standalone FastAPI service responsible for tracking the lifecycle and status of Quadracode agents. It provides a centralized registration and discovery mechanism, allowing the orchestrator and other system components to maintain an up-to-date roster of active agents. The service is backed by a simple SQLite database, making it easy to deploy and manage.

## Architectural Overview

The service is designed with a clean, layered architecture that separates concerns into distinct modules:

- **API Layer (`api.py`)**: Defines the HTTP endpoints for all agent management operations.
- **Service Layer (`service.py`)**: Encapsulates the core business logic for agent registration, health monitoring, and lifecycle management.
- **Data Access Layer (`database.py`)**: Provides a thread-safe interface to the SQLite database.
- **Configuration (`config.py`)**: Manages all application settings using a Pydantic-based model.
- **Data Schemas (`schemas.py`)**: Defines the Pydantic models for API requests and responses.
- **Application Factory (`app.py`)**: Wires all the components together to create the FastAPI application instance.
- **Entry Point (`main.py`)**: Runs the application using a `uvicorn` server.

## Core Components

### 1. API Layer (`api.py`)

The API is built using FastAPI's `APIRouter`. This modular approach allows the API endpoints to be defined independently of the main application, making the codebase more organized. The router includes endpoints for:

- **Health Checks**: A simple `/health` endpoint for monitoring the service's status.
- **Agent Registration**: `POST /agents/register` to register a new agent.
- **Heartbeats**: `POST /agents/{agent_id}/heartbeat` for agents to report their liveness.
- **Agent Listing**: `GET /agents` to retrieve a list of all agents, with options to filter by health or hotpath status.
- **Hotpath Management**: Endpoints for listing and setting an agent's `hotpath` status, which marks it as a resident, non-scalable agent.
- **Statistics**: `GET /stats` to retrieve aggregate statistics about the registry.

### 2. Service Layer (`service.py`)

The `AgentRegistryService` class is the heart of the application, containing all the business logic. It is initialized with a `Database` instance and `RegistrySettings`, which it uses to perform its functions. Key responsibilities include:

- **Agent Registration**: Handling the `register` operation by upserting agent data into the database.
- **Health Monitoring**: The `_is_healthy` method determines an agent's health based on its last heartbeat, using a configurable timeout (`agent_timeout`).
- **Data Transformation**: The `_row_to_agent` method converts database rows into `AgentInfo` Pydantic models, ensuring a clean separation between the data and service layers.

### 3. Data Access Layer (`database.py`)

The `Database` class provides a simple, thread-safe interface to the SQLite database. It uses a context manager (`connect`) to handle connection and transaction management. The class is responsible for:

- **Schema Initialization**: The `init_schema` method creates the `agents` table and is designed to be idempotent. It also includes a non-destructive migration to add new columns.
- **CRUD Operations**: The class provides high-level methods for all database operations, such as `upsert_agent`, `update_heartbeat`, and `fetch_agents`. All SQL is encapsulated within this layer.

### 4. Configuration (`config.py`)

The `RegistrySettings` class, which inherits from Pydantic's `BaseSettings`, defines the application's configuration. This allows settings to be loaded from environment variables, providing a flexible deployment model. Key settings include:

- `registry_port`: The port for the FastAPI server.
- `database_path`: The path to the SQLite database file.
- `agent_timeout`: The threshold for determining agent health.

### 5. Data Schemas (`schemas.py`)

This module defines all the Pydantic models for the API. These schemas serve as the data contracts for the service, ensuring that all incoming and outgoing data is validated. Key models include:

- `AgentRegistrationRequest`: The payload for registering a new agent.
- `AgentHeartbeat`: The payload for agent heartbeats.
- `AgentInfo`: The full data model for an agent, used in API responses.
- `AgentListResponse`: A wrapper for lists of agents returned by the API.

### 6. Application Factory and Entry Point (`app.py` and `main.py`)

The `create_app` function in `app.py` acts as a factory for the FastAPI application. It initializes all the components (settings, database, service) and wires them together. The `main.py` script then uses this factory to create the app and runs it with `uvicorn`. This separation of concerns makes the application easy to test and run.

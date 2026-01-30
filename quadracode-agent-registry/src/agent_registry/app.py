"""
This module is responsible for creating and configuring the FastAPI application.

It handles the initialization of all major components, including settings, 
database connections, and the agent registry service. The `create_app` function 
serves as the main factory for the application, ensuring that all dependencies 
are properly wired up before the application starts. This centralized setup 
simplifies the application's entry point and makes it easier to manage 
dependencies.
"""
import logging

from fastapi import FastAPI

from .api import get_router
from .config import RegistrySettings
from .database import Database
from .service import AgentRegistryService

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """
    Factory function to create and configure the FastAPI application.

    This function orchestrates the entire setup of the agent registry service. 
    It initializes the application settings, sets up the database schema, 
    creates an instance of the `AgentRegistryService`, and mounts the API 
    router. The fully configured FastAPI application instance is then returned.

    Returns:
        A fully configured `FastAPI` application instance.
    """
    logging.basicConfig(level=logging.INFO)

    # Initialize settings, database, and service synchronously
    settings = RegistrySettings()
    
    # Log mock mode status
    if settings.quadracode_mock_mode:
        logger.info("QUADRACODE_MOCK_MODE=true: Using in-memory SQLite database")
    else:
        logger.info(f"Using SQLite database at: {settings.database_path}")
    
    db = Database(settings.database_path)
    db.init_schema()
    service = AgentRegistryService(db=db, settings=settings)

    description = "Lightweight registry for Quadracode agents (SQLite-backed)"
    if settings.quadracode_mock_mode:
        description += " [MOCK MODE: in-memory database]"

    app = FastAPI(
        title="Quadracode Agent Registry",
        description=description,
        version="1.0.0",
    )

    # Attach to app state for potential future use
    app.state.settings = settings
    app.state.db = db
    app.state.service = service

    # Mount API routes
    app.include_router(get_router(service))

    return app


# Create the ASGI app instance for uvicorn
app = create_app()

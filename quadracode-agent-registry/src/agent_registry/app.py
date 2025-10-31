import logging

from fastapi import FastAPI

from .api import get_router
from .config import RegistrySettings
from .database import Database
from .service import AgentRegistryService


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)

    # Initialize settings, database, and service synchronously
    settings = RegistrySettings()
    db = Database(settings.database_path)
    db.init_schema()
    service = AgentRegistryService(db=db, settings=settings)

    app = FastAPI(
        title="Quadracode Agent Registry",
        description="Lightweight registry for Quadracode agents (SQLite-backed)",
        version="1.0.0",
    )

    # Attach to app state for potential future use
    app.state.settings = settings
    app.state.db = db
    app.state.service = service

    # Mount API routes
    app.include_router(get_router(service))

    return app

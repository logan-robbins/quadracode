"""FastAPI application factory for the Quadracode Agent Registry.

Uses the modern ``lifespan`` async-context-manager pattern (FastAPI >= 0.95)
instead of the deprecated ``@app.on_event`` hooks.  All heavyweight
initialisation (settings, database, service) happens inside the lifespan so
that importing this module is side-effect-free.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import get_router
from .config import RegistrySettings
from .database import Database
from .service import AgentRegistryService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown resources for the registry.

    On startup: load settings, initialise the database schema, wire up the
    service layer, and mount the API router.

    On shutdown: (currently no teardown needed — SQLite connections close
    on garbage collection).
    """
    logging.basicConfig(level=logging.INFO, force=True)

    settings = RegistrySettings()

    if settings.quadracode_mock_mode:
        logger.info("QUADRACODE_MOCK_MODE=true: using in-memory SQLite database")
    else:
        logger.info("Using SQLite database at: %s", settings.database_path)

    db = Database(settings.database_path)
    db.init_schema()
    service = AgentRegistryService(db=db, settings=settings)

    # Store on app.state for introspection / testing
    application.state.settings = settings
    application.state.db = db
    application.state.service = service

    # Mount API routes
    application.include_router(get_router(service))

    logger.info("Agent Registry started on port %d", settings.registry_port)
    yield
    logger.info("Agent Registry shutting down")


def create_app() -> FastAPI:
    """Application factory.

    Returns:
        A fully configured ``FastAPI`` instance with lifespan management.
    """
    return FastAPI(
        title="Quadracode Agent Registry",
        description="Lightweight registry for Quadracode agents (SQLite-backed)",
        version="1.0.0",
        lifespan=lifespan,
    )


app = create_app()

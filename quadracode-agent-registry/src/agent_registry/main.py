"""Uvicorn entry point for the Agent Registry service."""

import logging

import uvicorn

from .config import RegistrySettings


def main() -> None:
    """Start the agent registry HTTP server."""
    logging.basicConfig(level=logging.INFO)
    settings = RegistrySettings()
    uvicorn.run(
        "agent_registry.app:app",
        host="0.0.0.0",
        port=settings.registry_port,
        reload=False,
    )


if __name__ == "__main__":
    main()

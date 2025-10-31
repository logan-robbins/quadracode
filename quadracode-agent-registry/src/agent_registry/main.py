import logging

import uvicorn

from .app import create_app
from .config import RegistrySettings


# ASGI application
app = create_app()


def main():
    """Main entry point."""
    logging.basicConfig(level=logging.INFO)
    settings = RegistrySettings()
    uvicorn.run(
        "agent_registry.main:app",
        host="0.0.0.0",
        port=settings.registry_port,
        reload=False,
    )


if __name__ == "__main__":
    main()

"""
This module serves as the main entry point for running the agent registry 
service.

It uses `uvicorn` to run the application as an ASGI server. This script is 
intended to be executed directly to start the service, and it handles the 
configuration of the server based on the application's settings.
"""
import logging

import uvicorn

from .config import RegistrySettings


def main():
    """
    Main entry point for starting the agent registry service.

    This function configures the logging, loads the application settings, and 
    starts the uvicorn server to serve the FastAPI application. It binds the 
    server to all available network interfaces (`0.0.0.0`) and listens on the 
    port specified in the `RegistrySettings`.
    """
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

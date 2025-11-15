"""
This module defines the configuration settings for the Agent Registry service.

It uses Pydantic's `BaseSettings` to create a strongly-typed settings object that 
can be populated from environment variables. This approach provides a centralized 
and validated source of configuration for the entire application, covering 
everything from server settings to database paths and health check parameters.
"""
from pydantic_settings import BaseSettings


class RegistrySettings(BaseSettings):
    """
    Configuration model for the Agent Registry service.

    This class defines all the configurable parameters for the agent registry, 
    including server settings, database connection details, and health check 
    thresholds. Pydantic automatically reads these settings from environment 
    variables, providing a flexible and robust configuration mechanism.

    Attributes:
        registry_port: The port on which the registry service will run.
        database_path: The file path for the SQLite database.
        agent_timeout: The time in seconds after which an agent is considered 
                       stale if no heartbeat is received.
    """

    # Server
    registry_port: int = 8090

    # SQLite database file path (relative or absolute)
    database_path: str = "./registry.db"

    # Health/heartbeat
    agent_timeout: int = 30  # seconds until an agent is considered stale

    class Config:
        env_prefix = ""  # allow direct env var mapping


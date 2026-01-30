"""
This module defines the configuration settings for the Agent Registry service.

It uses Pydantic's `BaseSettings` to create a strongly-typed settings object that 
can be populated from environment variables. This approach provides a centralized 
and validated source of configuration for the entire application, covering 
everything from server settings to database paths and health check parameters.
"""
from pydantic import model_validator
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
        quadracode_mock_mode: When True, uses in-memory SQLite database for
                              standalone testing without external dependencies.
    """

    # Server
    registry_port: int = 8090

    # SQLite database file path (relative or absolute)
    # When quadracode_mock_mode=True, this is overridden to ":memory:"
    database_path: str = "./registry.db"

    # Health/heartbeat
    agent_timeout: int = 30  # seconds until an agent is considered stale

    # Mock mode for standalone testing (no external dependencies)
    quadracode_mock_mode: bool = False

    @model_validator(mode="after")
    def configure_mock_mode(self) -> "RegistrySettings":
        """Override database_path to in-memory when mock mode is enabled."""
        if self.quadracode_mock_mode:
            object.__setattr__(self, "database_path", ":memory:")
        return self

    class Config:
        env_prefix = ""  # allow direct env var mapping

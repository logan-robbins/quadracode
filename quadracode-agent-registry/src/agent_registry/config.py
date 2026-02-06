"""Configuration settings for the Agent Registry service.

Uses Pydantic v2 BaseSettings with SettingsConfigDict for environment-variable-driven
configuration. All settings are validated at startup.
"""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RegistrySettings(BaseSettings):
    """Strongly-typed configuration for the Agent Registry service.

    Reads from environment variables with no prefix. When ``quadracode_mock_mode``
    is enabled, the database path is forced to ``":memory:"`` for isolated testing.

    Attributes:
        registry_port: TCP port the HTTP server binds to.
        database_path: Filesystem path for the SQLite database file.
        agent_timeout: Seconds without a heartbeat before an agent is marked stale.
        quadracode_mock_mode: When True, uses in-memory SQLite (no disk dependency).
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
    )

    registry_port: int = 8090
    database_path: str = "./registry.db"
    agent_timeout: int = 30
    quadracode_mock_mode: bool = False

    @model_validator(mode="after")
    def _configure_mock_mode(self) -> "RegistrySettings":
        """Force in-memory SQLite when mock mode is enabled."""
        if self.quadracode_mock_mode:
            object.__setattr__(self, "database_path", ":memory:")
        return self

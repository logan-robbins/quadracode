from pydantic_settings import BaseSettings


class RegistrySettings(BaseSettings):
    """Configuration for the Agent Registry service."""

    # Server
    registry_port: int = 8090

    # SQLite database file path (relative or absolute)
    database_path: str = "./registry.db"

    # Health/heartbeat
    agent_timeout: int = 30  # seconds until an agent is considered stale

    class Config:
        env_prefix = ""  # allow direct env var mapping


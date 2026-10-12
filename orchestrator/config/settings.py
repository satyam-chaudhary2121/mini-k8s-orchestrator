from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Control-plane configuration. Every value comes from the environment (or .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    api_host: str = "0.0.0.0"  # noqa: S104 - bind address is deployment configuration
    api_port: int = 8000
    log_level: str = "INFO"

    database_url: str
    redis_url: str | None = None  # reserved: only needed for multi-replica control planes

    heartbeat_interval: int = 10
    sync_interval: float = 3.0
    node_unhealthy_after: int = 30
    node_timeout: int = 60
    reconciliation_interval: float = 2.0
    controller_max_backoff: float = 30.0

    scheduler_strategy: str = "least_loaded"
    max_replicas: int = 500
    restart_backoff_base: float = 2.0
    restart_backoff_max: float = 300.0
    pod_retention_seconds: int = 3600
    agent_request_timeout: float = 5.0

    run_controllers: bool = True

    admin_api_key: SecretStr
    operator_api_key: SecretStr | None = None
    read_only_api_key: SecretStr | None = None
    worker_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

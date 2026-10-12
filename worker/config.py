from __future__ import annotations

import socket

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _total_memory_mb() -> int:
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 1024


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    control_plane_url: str
    worker_api_key: SecretStr
    node_hostname: str = socket.gethostname()
    advertise_ip: str = "127.0.0.1"
    agent_port: int = 9000
    agent_url: str | None = None
    node_cpu: float | None = None
    node_memory_mb: int | None = None
    node_labels: dict[str, str] = {}
    log_level: str = "INFO"
    request_timeout: float = 10.0

    @property
    def cpu(self) -> float:
        import os

        return self.node_cpu or float(os.cpu_count() or 1)

    @property
    def memory_mb(self) -> int:
        return self.node_memory_mb or _total_memory_mb()

    @property
    def public_agent_url(self) -> str:
        return self.agent_url or f"http://{self.advertise_ip}:{self.agent_port}"

"""Container runtime abstraction. The agent depends on this, never on the Docker SDK."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

LABEL_MANAGED = "orchestrator.managed"
LABEL_POD_ID = "orchestrator.pod-id"
LABEL_POD_NAME = "orchestrator.pod-name"
LABEL_EPOCH = "orchestrator.epoch"


class RuntimeFailure(Exception):
    """Base class for runtime errors."""


class RuntimeUnavailableError(RuntimeFailure):
    """Transient: daemon unreachable, timeout, 5xx. Safe to retry."""


class ImageNotFoundError(RuntimeFailure):
    """Permanent: the image does not exist or access is denied."""


class ContainerCreateError(RuntimeFailure):
    """Permanent: the daemon rejected the container configuration."""


class ContainerNotFoundError(RuntimeFailure):
    pass


@dataclass(frozen=True)
class ContainerSpec:
    name: str
    image: str
    command: list[str] | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    ports: list[int] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    cpu: float | None = None
    memory_mb: int | None = None


@dataclass(frozen=True)
class ContainerInfo:
    id: str
    name: str
    status: str  # created | running | exited | restarting | paused | dead | removing
    exit_code: int | None = None
    labels: dict[str, str] = field(default_factory=dict)
    ports: dict[int, int] = field(default_factory=dict)  # container port -> published host port
    started_at: datetime | None = None


class ContainerRuntime(ABC):
    @property
    @abstractmethod
    def host(self) -> str:
        """Address at which published container ports are reachable from the agent."""

    @abstractmethod
    def info(self) -> dict[str, Any]: ...

    @abstractmethod
    def pull_image(self, image: str) -> None: ...

    @abstractmethod
    def create_container(self, spec: ContainerSpec) -> str: ...

    @abstractmethod
    def start_container(self, container_id: str) -> None: ...

    @abstractmethod
    def stop_container(self, container_id: str, timeout: int = 10) -> None: ...

    @abstractmethod
    def restart_container(self, container_id: str, timeout: int = 10) -> None: ...

    @abstractmethod
    def remove_container(self, container_id: str, force: bool = True) -> None: ...

    @abstractmethod
    def inspect_container(self, container_id: str) -> ContainerInfo: ...

    @abstractmethod
    def get_logs(self, container_id: str, tail: int = 100) -> str: ...

    @abstractmethod
    def list_containers(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]: ...

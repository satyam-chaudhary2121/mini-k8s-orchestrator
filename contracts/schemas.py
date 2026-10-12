"""Wire contracts shared by the API server, the worker agent and the CLI."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NAME_PATTERN = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
MAX_NAME_LENGTH = 53

RestartPolicy = Literal["Always", "OnFailure", "Never"]
ReportedStatus = Literal["CREATING", "RUNNING", "FAILED", "TERMINATED"]


class PortSpec(BaseModel):
    container_port: int = Field(ge=1, le=65535)
    protocol: Literal["TCP", "UDP"] = "TCP"


class HealthCheckSpec(BaseModel):
    type: Literal["http"] = "http"
    path: str = Field(default="/", pattern=r"^/")
    port: int = Field(ge=1, le=65535)
    interval: int = Field(default=10, ge=1, le=3600)
    timeout: int = Field(default=3, ge=1, le=60)
    failure_threshold: int = Field(default=3, ge=1, le=20)


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH, pattern=NAME_PATTERN)
    image: str = Field(min_length=1, max_length=255)
    replicas: int = Field(default=1, ge=0)
    cpu_request: float = Field(default=0.1, gt=0, le=256)
    memory_request: int = Field(default=128, gt=0, description="MiB")
    ports: list[PortSpec] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    command: list[str] | None = None
    args: list[str] = Field(default_factory=list)
    restart_policy: RestartPolicy = "Always"
    health_check: HealthCheckSpec | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    node_selector: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _health_port_is_exposed(self) -> Self:
        if self.health_check and self.health_check.port not in {
            p.container_port for p in self.ports
        }:
            raise ValueError("health_check.port must be one of the declared container ports")
        return self

    def template(self) -> dict[str, Any]:
        """The pod template: everything except identity and replica count."""
        return self.model_dump(exclude={"name", "replicas"})


class ScaleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int = Field(ge=0)


class ServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH, pattern=NAME_PATTERN)
    selector: dict[str, str] = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    target_port: int = Field(ge=1, le=65535)
    lb_policy: Literal["round_robin"] = "round_robin"


class NodeRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: str = Field(min_length=1, max_length=255)
    ip: str = Field(min_length=1, max_length=255)
    cpu: float = Field(gt=0)
    memory: int = Field(gt=0, description="MiB")
    labels: dict[str, str] = Field(default_factory=dict)
    agent_url: str | None = None
    runtime_info: dict[str, Any] = Field(default_factory=dict)


class NodeRegisterResponse(BaseModel):
    node_id: str
    heartbeat_interval: int
    sync_interval: float


class HeartbeatRequest(BaseModel):
    runtime_info: dict[str, Any] | None = None
    running_containers: int | None = Field(default=None, ge=0)


class PodStatusReport(BaseModel):
    """Observed state of one pod, reported by the worker agent that runs it."""

    model_config = ConfigDict(extra="forbid")

    status: ReportedStatus
    reason: str | None = Field(default=None, max_length=128)
    message: str | None = Field(default=None, max_length=1024)
    exit_code: int | None = None
    container_id: str | None = None
    ports: dict[str, int] = Field(default_factory=dict, description="container port -> host port")
    healthy: bool | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    permanent: bool = False

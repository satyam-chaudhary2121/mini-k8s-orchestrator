from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class NodeOut(ORM):
    id: str
    hostname: str
    ip: str
    status: str
    cordoned: bool
    cpu_capacity: float
    memory_capacity_mb: int
    cpu_allocated: float = 0.0
    memory_allocated_mb: int = 0
    pod_count: int = 0
    labels: dict[str, str]
    runtime_info: dict[str, Any]
    last_heartbeat_at: datetime | None
    drain_completed_at: datetime | None
    created_at: datetime


class PodOut(ORM):
    id: str
    name: str
    deployment_id: str
    node: str | None = None
    node_id: str | None
    status: str
    desired_state: str
    reason: str | None
    message: str | None
    ready: bool
    restart_count: int
    exit_code: int | None
    labels: dict[str, str]
    port_map: dict[str, int]
    cpu_request: float
    memory_request_mb: int
    created_at: datetime
    last_transition_at: datetime


class ServiceOut(ORM):
    id: str
    name: str
    selector: dict[str, str]
    port: int
    target_port: int
    lb_policy: str
    created_at: datetime


class EndpointOut(BaseModel):
    pod: str
    pod_id: str
    address: str
    port: int


class ServiceDetail(ServiceOut):
    endpoints: list[EndpointOut]


class EventOut(ORM):
    id: int
    type: str
    object_kind: str
    object_id: str
    message: str
    payload: dict[str, Any]
    created_at: datetime

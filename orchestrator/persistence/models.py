from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class NodeStatus(StrEnum):
    REGISTERING = "REGISTERING"
    READY = "READY"
    UNHEALTHY = "UNHEALTHY"
    DRAINING = "DRAINING"
    OFFLINE = "OFFLINE"


class PodStatus(StrEnum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    CREATING = "CREATING"
    RUNNING = "RUNNING"
    UNHEALTHY = "UNHEALTHY"
    FAILED = "FAILED"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"


class DesiredState(StrEnum):
    RUNNING = "RUNNING"
    TERMINATED = "TERMINATED"


class DeploymentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETING = "DELETING"


def _enum_check(column: str, enum: type[StrEnum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{m.value}'" for m in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Node(TimestampMixin, Base):
    __tablename__ = "nodes"
    __table_args__ = (
        _enum_check("status", NodeStatus, "ck_nodes_status"),
        CheckConstraint("cpu_capacity > 0 AND memory_capacity_mb > 0", name="ck_nodes_capacity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    ip: Mapped[str] = mapped_column(String(255), nullable=False)
    agent_url: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(
        String(16), default=NodeStatus.REGISTERING.value, index=True, nullable=False
    )
    cordoned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cpu_capacity: Mapped[float] = mapped_column(Float, nullable=False)
    memory_capacity_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    labels: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    runtime_info: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drain_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Deployment(TimestampMixin, Base):
    __tablename__ = "deployments"
    __table_args__ = (
        _enum_check("status", DeploymentStatus, "ck_deployments_status"),
        CheckConstraint("desired_replicas >= 0", name="ck_deployments_replicas"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(53), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=DeploymentStatus.ACTIVE.value)
    template: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    desired_replicas: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Pod(TimestampMixin, Base):
    __tablename__ = "pods"
    __table_args__ = (
        _enum_check("status", PodStatus, "ck_pods_status"),
        _enum_check("desired_state", DesiredState, "ck_pods_desired_state"),
        CheckConstraint("cpu_request > 0 AND memory_request_mb > 0", name="ck_pods_requests"),
        Index("ix_pods_node_status", "node_id", "status"),
        Index("ix_pods_deployment_desired", "deployment_id", "desired_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    deployment_id: Mapped[str] = mapped_column(
        ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(
        String(16), default=PodStatus.PENDING.value, index=True, nullable=False
    )
    desired_state: Mapped[str] = mapped_column(String(16), default=DesiredState.RUNNING.value)
    reason: Mapped[str | None] = mapped_column(String(128))
    message: Mapped[str | None] = mapped_column(Text)
    permanent_failure: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    labels: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, nullable=False)
    cpu_request: Mapped[float] = mapped_column(Float, nullable=False)
    memory_request_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    restart_policy: Mapped[str] = mapped_column(String(16), default="Always", nullable=False)
    restart_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    restart_epoch: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    port_map: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict, nullable=False)
    last_transition_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Container(TimestampMixin, Base):
    __tablename__ = "containers"
    __table_args__ = (UniqueConstraint("pod_id", "epoch", name="uq_containers_pod_epoch"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pod_id: Mapped[str] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_id: Mapped[str] = mapped_column(String(128), nullable=False)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceAllocation(TimestampMixin, Base):
    """Source of truth for how much of a node's capacity is spoken for."""

    __tablename__ = "resource_allocations"
    __table_args__ = (CheckConstraint("cpu >= 0 AND memory_mb >= 0", name="ck_allocations_nonneg"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pod_id: Mapped[str] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    cpu: Mapped[float] = mapped_column(Float, nullable=False)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False)


class HealthCheckState(TimestampMixin, Base):
    __tablename__ = "health_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pod_id: Mapped[str] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_result: Mapped[bool | None] = mapped_column(Boolean)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Service(TimestampMixin, Base):
    __tablename__ = "services"
    __table_args__ = (CheckConstraint("port BETWEEN 1 AND 65535", name="ck_services_port"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(53), unique=True, nullable=False)
    selector: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    target_port: Mapped[int] = mapped_column(Integer, nullable=False)
    lb_policy: Mapped[str] = mapped_column(String(32), default="round_robin", nullable=False)


class ServiceEndpoint(TimestampMixin, Base):
    __tablename__ = "service_endpoints"
    __table_args__ = (UniqueConstraint("service_id", "pod_id", name="uq_endpoints_service_pod"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_id: Mapped[str] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pod_id: Mapped[str] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)


class Event(Base):
    """Append-only audit/debug log. No foreign keys so history survives deletions."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_object", "object_kind", "object_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    object_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ApiKey(TimestampMixin, Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)

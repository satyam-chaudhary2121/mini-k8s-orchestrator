from datetime import datetime

from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    address: Mapped[str] = mapped_column(String(200))
    cpu_capacity: Mapped[int] = mapped_column(Integer, default=4)
    memory_capacity_mb: Mapped[int] = mapped_column(Integer, default=4096)
    status: Mapped[str] = mapped_column(String(20), default="Ready")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    pods: Mapped[list["Pod"]] = relationship(back_populates="node")


class Workload(Base):
    __tablename__ = "workloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    image: Mapped[str] = mapped_column(String(200))
    replicas: Mapped[int] = mapped_column(Integer, default=1)

    pods: Mapped[list["Pod"]] = relationship(back_populates="workload")


class Pod(Base):
    __tablename__ = "pods"

    id: Mapped[int] = mapped_column(primary_key=True)
    workload_id: Mapped[int] = mapped_column(ForeignKey("workloads.id"))
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"), nullable=True)
    container_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="Pending")
    restart_count: Mapped[int] = mapped_column(Integer, default=0)

    workload: Mapped["Workload"] = relationship(back_populates="pods")
    node: Mapped["Node | None"] = relationship(back_populates="pods")
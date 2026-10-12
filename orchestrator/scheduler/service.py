"""Database-backed scheduling pass: bind PENDING pods to nodes."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orchestrator import metrics
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.db import Database
from orchestrator.persistence.models import (
    DesiredState,
    Node,
    Pod,
    PodStatus,
    ResourceAllocation,
)
from orchestrator.scheduler.strategies import (
    NodeView,
    PodRequest,
    SchedulerStrategy,
    filter_nodes,
)
from orchestrator.services.pods import transition_pod

log = logging.getLogger("orchestrator.scheduler")
BATCH_SIZE = 100


def node_views(session: Session) -> list[NodeView]:
    allocated = (
        select(
            ResourceAllocation.node_id,
            func.coalesce(func.sum(ResourceAllocation.cpu), 0.0).label("cpu"),
            func.coalesce(func.sum(ResourceAllocation.memory_mb), 0).label("mem"),
            func.count().label("pods"),
        )
        .group_by(ResourceAllocation.node_id)
        .subquery()
    )
    rows = session.execute(
        select(Node, allocated.c.cpu, allocated.c.mem, allocated.c.pods)
        .outerjoin(allocated, allocated.c.node_id == Node.id)
        .order_by(Node.hostname)
    ).all()
    return [
        NodeView(
            id=n.id,
            hostname=n.hostname,
            status=n.status,
            cordoned=n.cordoned,
            labels=n.labels,
            cpu_capacity=n.cpu_capacity,
            memory_capacity_mb=n.memory_capacity_mb,
            cpu_allocated=float(cpu or 0.0),
            memory_allocated_mb=int(mem or 0),
            pod_count=int(pods or 0),
        )
        for n, cpu, mem, pods in rows
    ]


def _bind(session: Session, pod: Pod, node_id: str) -> bool:
    """Atomically claim capacity. The node row lock serialises concurrent schedulers, and the
    capacity is re-checked under the lock so a stale view can never over-commit a node."""
    node = session.get(Node, node_id, with_for_update=True)
    if node is None or node.status != "READY" or node.cordoned:
        return False
    cpu, mem = session.execute(
        select(
            func.coalesce(func.sum(ResourceAllocation.cpu), 0.0),
            func.coalesce(func.sum(ResourceAllocation.memory_mb), 0),
        ).where(ResourceAllocation.node_id == node_id)
    ).one()
    if node.cpu_capacity - float(cpu) < pod.cpu_request:
        return False
    if node.memory_capacity_mb - int(mem) < pod.memory_request_mb:
        return False
    session.add(
        ResourceAllocation(
            node_id=node_id, pod_id=pod.id, cpu=pod.cpu_request, memory_mb=pod.memory_request_mb
        )
    )
    pod.node_id = node_id
    transition_pod(session, pod, PodStatus.SCHEDULED, message=f"assigned to {node.hostname}")
    return True


def schedule_one(session: Session, pod: Pod, strategy: SchedulerStrategy) -> bool:
    request = PodRequest(pod.cpu_request, pod.memory_request_mb, pod.spec.get("node_selector", {}))
    nodes = node_views(session)
    result = filter_nodes(nodes, request)
    chosen = strategy.select(result.candidates, request) if result.candidates else None
    metrics.SCHEDULER_ATTEMPTS.inc()
    if chosen is None or not _bind(session, pod, chosen.id):
        reason = (
            f"Unschedulable: {result.explain(len(nodes))}"
            if chosen is None
            else "Unschedulable: lost race"
        )
        metrics.SCHEDULER_FAILURES.inc()
        if pod.message != reason:  # only emit when the explanation changes (avoids event spam)
            pod.reason, pod.message = "Unschedulable", reason
            emit(session, EventType.POD_UNSCHEDULABLE, "pod", pod.id, reason, pod=pod.name)
        return False
    return True


def schedule_pending(db: Database, strategy: SchedulerStrategy) -> int:
    """One scheduling pass. Each pod is bound in its own transaction so one bad pod cannot
    block the rest. Returns the number of pods scheduled."""
    with db.transaction() as session:
        pod_ids = list(
            session.scalars(
                select(Pod.id)
                .where(
                    Pod.status == PodStatus.PENDING.value,
                    Pod.desired_state == DesiredState.RUNNING.value,
                )
                .order_by(Pod.created_at)
                .limit(BATCH_SIZE)
            )
        )
    scheduled = 0
    for pod_id in pod_ids:
        with db.transaction() as session:
            pod = session.scalars(
                select(Pod).where(Pod.id == pod_id).with_for_update(skip_locked=True)
            ).first()
            if pod is None or pod.status != PodStatus.PENDING.value:
                continue
            scheduled += schedule_one(session, pod, strategy)
    return scheduled

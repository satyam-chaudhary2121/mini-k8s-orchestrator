from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from contracts.schemas import NodeRegister
from orchestrator.common.errors import ConflictError, NotFoundError
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.models import (
    DesiredState,
    Node,
    NodeStatus,
    Pod,
    PodStatus,
    ResourceAllocation,
    utcnow,
)
from orchestrator.services.pods import ACTIVE_STATUSES, transition_pod

NODE_LOST = "NodeLost"


def get_node(session: Session, ident: str, *, lock: bool = False) -> Node:
    """Look up a node by id or hostname."""
    stmt = select(Node).where(or_(Node.id == ident, Node.hostname == ident))
    if lock:
        stmt = stmt.with_for_update()
    node = session.scalars(stmt).first()
    if node is None:
        raise NotFoundError(f"node {ident!r} does not exist", code="NODE_NOT_FOUND")
    return node


def register_node(session: Session, body: NodeRegister) -> Node:
    """Idempotent by hostname: a restarted worker gets its existing node (and workloads) back."""
    node = session.scalars(
        select(Node).where(Node.hostname == body.hostname).with_for_update()
    ).first()
    created = node is None
    if node is None:
        node = Node(hostname=body.hostname)
        session.add(node)
    node.ip, node.agent_url = body.ip, body.agent_url
    node.cpu_capacity, node.memory_capacity_mb = body.cpu, body.memory
    node.labels, node.runtime_info = body.labels, body.runtime_info
    node.last_heartbeat_at = utcnow()
    node.status = NodeStatus.DRAINING.value if node.cordoned else NodeStatus.READY.value
    session.flush()
    emit(
        session,
        EventType.NODE_REGISTERED,
        "node",
        node.id,
        f"node {node.hostname} {'registered' if created else 're-registered'}",
        hostname=node.hostname,
        cpu=body.cpu,
        memory=body.memory,
    )
    return node


def record_heartbeat(session: Session, node_id: str, runtime_info: dict[str, Any] | None) -> Node:
    node = get_node(session, node_id, lock=True)
    node.last_heartbeat_at = utcnow()
    if runtime_info is not None:
        node.runtime_info = runtime_info
    previous = node.status
    if previous in (
        NodeStatus.UNHEALTHY.value,
        NodeStatus.OFFLINE.value,
        NodeStatus.REGISTERING.value,
    ):
        node.status = NodeStatus.DRAINING.value if node.cordoned else NodeStatus.READY.value
        emit(
            session,
            EventType.NODE_READY,
            "node",
            node.id,
            f"node {node.hostname} recovered ({previous} -> {node.status})",
        )
    return node


def mark_node_lost(session: Session, node: Node) -> int:
    """Node stopped heartbeating: fail its workloads so the controllers replace them."""
    node.status = NodeStatus.OFFLINE.value
    emit(
        session,
        EventType.NODE_OFFLINE,
        "node",
        node.id,
        f"node {node.hostname} is offline (no heartbeat)",
        hostname=node.hostname,
    )
    pods = session.scalars(
        select(Pod)
        .where(
            Pod.node_id == node.id,
            Pod.status.in_(
                [s.value for s in (*ACTIVE_STATUSES, PodStatus.FAILED, PodStatus.TERMINATING)]
            ),
        )
        .with_for_update()
    ).all()
    for pod in pods:
        pod.desired_state = DesiredState.TERMINATED.value
        status = PodStatus(pod.status)
        if status is PodStatus.TERMINATING:
            transition_pod(session, pod, PodStatus.TERMINATED, reason=NODE_LOST)
        elif status is not PodStatus.FAILED:
            transition_pod(
                session,
                pod,
                PodStatus.FAILED,
                reason=NODE_LOST,
                message=f"node {node.hostname} stopped sending heartbeats",
            )
    return len(pods)


def drain_node(session: Session, ident: str) -> Node:
    """Cordon the node and ask every pod on it to terminate; the deployment controller
    recreates them elsewhere because the scheduler no longer considers this node."""
    node = get_node(session, ident, lock=True)
    if node.status != NodeStatus.OFFLINE.value:
        node.status = NodeStatus.DRAINING.value
    node.cordoned = True
    node.drain_completed_at = None
    session.execute(
        update(Pod)
        .where(Pod.node_id == node.id, Pod.status != PodStatus.TERMINATED.value)
        .values(desired_state=DesiredState.TERMINATED.value)
    )
    emit(session, EventType.NODE_DRAINING, "node", node.id, f"node {node.hostname} is draining")
    return node


def uncordon_node(session: Session, ident: str) -> Node:
    node = get_node(session, ident, lock=True)
    node.cordoned = False
    node.drain_completed_at = None
    if node.status == NodeStatus.DRAINING.value:
        node.status = NodeStatus.READY.value
        emit(session, EventType.NODE_READY, "node", node.id, f"node {node.hostname} uncordoned")
    return node


def delete_node(session: Session, ident: str, *, force: bool) -> None:
    node = get_node(session, ident, lock=True)
    live = session.scalar(
        select(func.count())
        .select_from(Pod)
        .where(Pod.node_id == node.id, Pod.status != PodStatus.TERMINATED.value)
    )
    if live and not force:
        raise ConflictError(
            f"node {node.hostname} still has {live} pod(s); drain it or use force",
            code="NODE_NOT_EMPTY",
        )
    if live:
        mark_node_lost(session, node)
    session.query(ResourceAllocation).filter(ResourceAllocation.node_id == node.id).delete()
    emit(session, EventType.NODE_DELETED, "node", node.id, f"node {node.hostname} deleted")
    session.delete(node)

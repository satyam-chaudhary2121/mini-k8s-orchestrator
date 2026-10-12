from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from orchestrator.config.settings import Settings
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.db import Database
from orchestrator.persistence.models import Node, NodeStatus, Pod, PodStatus, utcnow
from orchestrator.services.nodes import mark_node_lost


def reconcile_nodes(db: Database, settings: Settings) -> None:
    """Heartbeat staleness -> UNHEALTHY -> OFFLINE (workloads lost), and drain completion.

    Thresholds: age > node_unhealthy_after => UNHEALTHY; age > node_timeout => OFFLINE.
    Each node is handled in its own transaction under a row lock, and staleness is re-checked
    after locking so a heartbeat that commits first always wins.
    """
    with db.transaction() as session:
        ids = list(session.scalars(select(Node.id).where(Node.status != NodeStatus.OFFLINE.value)))
    for node_id in ids:
        with db.transaction() as session:
            node = session.get(Node, node_id, with_for_update=True)
            if node is None or node.status == NodeStatus.OFFLINE.value:
                continue
            now = utcnow()
            age = now - (node.last_heartbeat_at or node.created_at)
            if age > timedelta(seconds=settings.node_timeout):
                mark_node_lost(session, node)
            elif age > timedelta(seconds=settings.node_unhealthy_after):
                if node.status == NodeStatus.READY.value:
                    node.status = NodeStatus.UNHEALTHY.value
                    emit(
                        session,
                        EventType.NODE_UNHEALTHY,
                        "node",
                        node.id,
                        f"node {node.hostname} missed heartbeats",
                        age_seconds=age.total_seconds(),
                    )
            elif node.status == NodeStatus.DRAINING.value and node.drain_completed_at is None:
                remaining = session.scalar(
                    select(func.count())
                    .select_from(Pod)
                    .where(Pod.node_id == node.id, Pod.status != PodStatus.TERMINATED.value)
                )
                if remaining == 0:
                    node.drain_completed_at = now
                    emit(
                        session,
                        EventType.NODE_DRAINED,
                        "node",
                        node.id,
                        f"node {node.hostname} drained; safe for maintenance",
                    )

from __future__ import annotations

from sqlalchemy import select

from orchestrator.config.settings import Settings
from orchestrator.persistence.db import Database
from orchestrator.persistence.models import (
    DesiredState,
    Node,
    NodeStatus,
    Pod,
    PodStatus,
    Service,
    ServiceEndpoint,
)


def selector_matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())


def reconcile_endpoints(db: Database, settings: Settings) -> None:
    """Make service_endpoints equal to the set of ready, matching pods (adds and removes)."""
    with db.transaction() as session:
        services = list(session.scalars(select(Service)))
        rows = session.execute(
            select(Pod, Node.ip)
            .join(Node, Node.id == Pod.node_id)
            .where(
                Pod.status == PodStatus.RUNNING.value,
                Pod.ready.is_(True),
                Pod.desired_state == DesiredState.RUNNING.value,
                Node.status != NodeStatus.OFFLINE.value,
            )
        ).all()
        for service in services:
            wanted: dict[str, tuple[str, int]] = {}
            for pod, ip in rows:
                host_port = pod.port_map.get(str(service.target_port))
                if host_port and selector_matches(service.selector, pod.labels):
                    wanted[pod.id] = (ip, host_port)
            existing = {
                e.pod_id: e
                for e in session.scalars(
                    select(ServiceEndpoint).where(ServiceEndpoint.service_id == service.id)
                )
            }
            for pod_id, endpoint in existing.items():
                if pod_id not in wanted:
                    session.delete(endpoint)
                elif (endpoint.address, endpoint.port) != wanted[pod_id]:
                    endpoint.address, endpoint.port = wanted[pod_id]
            for pod_id, (address, port) in wanted.items():
                if pod_id not in existing:
                    session.add(
                        ServiceEndpoint(
                            service_id=service.id, pod_id=pod_id, address=address, port=port
                        )
                    )

"""Service discovery and load-balancing abstractions with simple real implementations.

Limitation (documented): this is a client-side resolver, not a kube-proxy style data plane.
Clients ask the API for the next endpoint; no traffic flows through the control plane.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestrator.persistence.models import Pod, Service, ServiceEndpoint


@dataclass(frozen=True)
class Endpoint:
    pod_id: str
    pod_name: str
    address: str
    port: int

    @property
    def url(self) -> str:
        return f"{self.address}:{self.port}"


class ServiceDiscovery(ABC):
    @abstractmethod
    def endpoints(self, session: Session, service: Service) -> list[Endpoint]: ...


class DatabaseServiceDiscovery(ServiceDiscovery):
    """Reads the endpoint registry maintained by the endpoint controller (only ready pods)."""

    def endpoints(self, session: Session, service: Service) -> list[Endpoint]:
        rows = session.execute(
            select(ServiceEndpoint, Pod.name)
            .join(Pod, Pod.id == ServiceEndpoint.pod_id)
            .where(ServiceEndpoint.service_id == service.id)
            .order_by(Pod.name)
        ).all()
        return [Endpoint(e.pod_id, name, e.address, e.port) for e, name in rows]


class LoadBalancer(ABC):
    @abstractmethod
    def pick(self, service_name: str, endpoints: Sequence[Endpoint]) -> Endpoint | None: ...


class RoundRobinBalancer(LoadBalancer):
    """Per-service rotating counter. Because endpoints are already health-filtered, selection is
    health-aware by construction. State is process-local (documented limitation)."""

    def __init__(self) -> None:
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def pick(self, service_name: str, endpoints: Sequence[Endpoint]) -> Endpoint | None:
        if not endpoints:
            return None
        with self._lock:
            index = self._counters[service_name] % len(endpoints)
            self._counters[service_name] += 1
        return endpoints[index]

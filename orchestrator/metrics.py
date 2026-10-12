"""Prometheus metrics. Counters are incremented where events happen; gauges are computed from
the database at scrape time so they always reflect persisted cluster state."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.orm import Session

REQUESTS = Counter("orchestrator_requests", "HTTP requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram(
    "orchestrator_request_duration_seconds", "HTTP request latency", ["method", "path"]
)
SCHEDULER_ATTEMPTS = Counter("scheduler_attempts", "Scheduling attempts")
SCHEDULER_FAILURES = Counter("scheduler_failures", "Scheduling attempts that found no node")
RECONCILIATIONS = Counter("reconciliation", "Controller reconciliation passes", ["controller"])
RECONCILIATION_FAILURES = Counter(
    "reconciliation_failures", "Failed controller reconciliation passes", ["controller"]
)
POD_RESTARTS = Counter("pods_restarts", "Pod restarts initiated by the control plane")

NODES_TOTAL = Gauge("nodes_total", "Registered nodes")
NODES_READY = Gauge("nodes_ready", "Nodes in READY state")
NODES_UNHEALTHY = Gauge("nodes_unhealthy", "Nodes in UNHEALTHY state")
NODES_OFFLINE = Gauge("nodes_offline", "Nodes in OFFLINE state")
PODS_TOTAL = Gauge("pods_total", "Pods not yet terminated")
PODS_RUNNING = Gauge("pods_running", "Pods in RUNNING state")
PODS_FAILED = Gauge("pods_failed", "Pods in FAILED state")
PODS_PENDING = Gauge("pods_pending", "Pods waiting for a node")


def refresh_gauges(session: Session) -> None:
    from orchestrator.persistence.models import Node, Pod

    nodes = dict(session.execute(select(Node.status, func.count()).group_by(Node.status)).all())
    pods = dict(session.execute(select(Pod.status, func.count()).group_by(Pod.status)).all())
    NODES_TOTAL.set(sum(nodes.values()))
    NODES_READY.set(nodes.get("READY", 0))
    NODES_UNHEALTHY.set(nodes.get("UNHEALTHY", 0))
    NODES_OFFLINE.set(nodes.get("OFFLINE", 0))
    PODS_TOTAL.set(sum(v for k, v in pods.items() if k != "TERMINATED"))
    PODS_RUNNING.set(pods.get("RUNNING", 0))
    PODS_FAILED.set(pods.get("FAILED", 0))
    PODS_PENDING.set(pods.get("PENDING", 0))

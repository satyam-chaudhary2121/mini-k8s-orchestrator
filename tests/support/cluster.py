"""Test harness: a real control plane (PostgreSQL + API) plus worker agents backed by FakeRuntime.

The controllers are driven by explicit ``tick()`` calls instead of timers, so tests are
deterministic. Everything between the API and the database is the real production code."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from orchestrator.config.settings import Settings
from orchestrator.controller import deployments, endpoints, nodes, pods
from orchestrator.persistence.db import Database
from orchestrator.persistence.models import Node, utcnow
from orchestrator.scheduler.service import schedule_pending
from orchestrator.scheduler.strategies import get_strategy
from tests.conftest import KEYS
from tests.support.fake_runtime import FakeHealthChecker, FakeRuntime
from worker.agent.agent import WorkerAgent
from worker.agent.client import ControlPlaneClient
from worker.config import WorkerSettings


@dataclass
class Worker:
    hostname: str
    agent: WorkerAgent
    runtime: FakeRuntime
    health: FakeHealthChecker
    alive: bool = True
    connected: bool = True
    http: TestClient | None = field(default=None, repr=False)


class Cluster:
    def __init__(self, app: Any, api: TestClient, db: Database, settings: Settings) -> None:
        self.app, self.api, self.db, self.settings = app, api, db, settings
        self.workers: dict[str, Worker] = {}
        self.strategy = get_strategy(settings.scheduler_strategy)

    # ---- workers
    def add_worker(
        self,
        hostname: str,
        cpu: float = 4,
        memory: int = 4096,
        labels: dict[str, str] | None = None,
    ) -> Worker:
        runtime, health = FakeRuntime(host=hostname), FakeHealthChecker()
        ws = WorkerSettings(
            _env_file=None,
            control_plane_url="http://testserver",
            worker_api_key=KEYS["worker"],
            node_hostname=hostname,
            advertise_ip=hostname,
            node_cpu=cpu,
            node_memory_mb=memory,
            node_labels=labels or {},
        )
        http = TestClient(self.app, headers={"Authorization": f"Bearer {KEYS['worker']}"})
        agent = WorkerAgent(ws, ControlPlaneClient(http), runtime, health)
        agent.register()
        worker = Worker(hostname, agent, runtime, health, http=http)
        self.workers[hostname] = worker
        return worker

    def stop_worker(self, hostname: str) -> None:
        """Simulate a worker machine dying: no more heartbeats, no more syncing."""
        self.workers[hostname].alive = False

    def expire_heartbeat(self, hostname: str, seconds: float) -> None:
        with self.db.transaction() as s:
            node = s.scalars(select(Node).where(Node.hostname == hostname)).one()
            node.last_heartbeat_at = utcnow() - timedelta(seconds=seconds)

    # ---- driving the system
    def tick(self, rounds: int = 3) -> None:
        for _ in range(rounds):
            for w in self.workers.values():
                if w.alive and w.connected:
                    w.agent.heartbeat_once()
            nodes.reconcile_nodes(self.db, self.settings)
            deployments.reconcile_deployments(self.db, self.settings)
            schedule_pending(self.db, self.strategy)
            pods.reconcile_pods(self.db, self.settings)
            for w in self.workers.values():
                if w.alive and w.connected:
                    w.agent.sync_once()
            endpoints.reconcile_endpoints(self.db, self.settings)

    # ---- API helpers (admin)
    def get(self, path: str, **kw: Any) -> Any:
        r = self.api.get(path, **kw)
        assert r.status_code == 200, r.text
        return r.json()

    def post(
        self, path: str, body: dict[str, Any] | None = None, expect: int | None = None, **kw: Any
    ) -> Any:
        r = self.api.post(path, json=body or {}, **kw)
        if expect:
            assert r.status_code == expect, r.text
        return r

    def pods(self, **params: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = self.get("/api/v1/pods", params=params)["items"]
        return items

    def deploy(self, name: str = "web", replicas: int = 3, **extra: Any) -> Any:
        body = {
            "name": name,
            "image": "nginx:alpine",
            "replicas": replicas,
            "cpu_request": 0.25,
            "memory_request": 256,
            "ports": [{"container_port": 80}],
            "health_check": {
                "type": "http",
                "path": "/",
                "port": 80,
                "interval": 1,
                "timeout": 1,
                "failure_threshold": 2,
            },
            **extra,
        }
        return self.post("/api/v1/deployments", body)

    def running_by_node(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.pods(status="RUNNING"):
            out[p["node"]] = out.get(p["node"], 0) + 1
        return out

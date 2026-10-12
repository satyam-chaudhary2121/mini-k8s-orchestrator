"""Control-plane resilience: controller loops survive database outages."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from orchestrator.controller.supervisor import Loop, Supervisor
from tests.conftest import headers_for


async def test_controller_loop_recovers_after_database_errors():
    """Simulates a PostgreSQL restart: run_once raises OperationalError, then recovers."""
    state = {"calls": 0, "ok": 0}

    def reconcile():
        state["calls"] += 1
        if state["calls"] <= 3:
            raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))
        state["ok"] += 1

    sup = Supervisor([Loop("deployment-controller", 0.01, reconcile)], max_backoff=0.05)
    sup.start()
    await asyncio.sleep(0.5)
    await sup.stop(timeout=2)
    assert state["ok"] >= 2


def test_api_stays_up_and_reports_not_ready_when_database_is_unreachable(app, client, monkeypatch):
    def boom():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(app.state.db, "ping", boom)
    assert client.get("/health").status_code == 200  # process is alive
    r = client.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}


def test_unhandled_errors_are_sanitised(app, client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internals")

    monkeypatch.setattr("orchestrator.api.routes.deployments.svc.get_deployment", boom)
    quiet = TestClient(app, raise_server_exceptions=False, headers=headers_for("admin"))
    r = quiet.get("/api/v1/deployments/x")
    assert r.status_code == 500 and "secret" not in r.text
    assert r.json()["error"]["code"] == "INTERNAL_ERROR" and r.json()["error"]["request_id"]

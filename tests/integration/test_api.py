from __future__ import annotations

from tests.conftest import headers_for


def test_missing_and_invalid_credentials(client):
    r = client.get("/api/v1/nodes", headers={"Authorization": ""})
    assert r.status_code == 401 and r.json()["error"]["code"] == "MISSING_CREDENTIALS"
    r = client.get("/api/v1/nodes", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    r = client.get("/api/v1/nodes", headers={"X-API-Key": "admin-key", "Authorization": ""})
    assert r.status_code == 200  # X-API-Key header is accepted too


def test_role_enforcement(client):
    body = {"name": "x", "image": "nginx"}
    assert (
        client.post("/api/v1/deployments", json=body, headers=headers_for("readonly")).status_code
        == 403
    )
    assert client.get("/api/v1/nodes", headers=headers_for("worker")).status_code == 403
    assert (
        client.post("/api/v1/deployments", json=body, headers=headers_for("operator")).status_code
        == 201
    )
    reg = {"hostname": "w", "ip": "1.1.1.1", "cpu": 1, "memory": 512}
    assert (
        client.post("/api/v1/nodes/register", json=reg, headers=headers_for("operator")).status_code
        == 403
    )
    assert (
        client.post("/api/v1/nodes/register", json=reg, headers=headers_for("worker")).status_code
        == 200
    )
    assert client.delete("/api/v1/nodes/w", headers=headers_for("operator")).status_code == 403
    assert client.delete("/api/v1/nodes/w", headers=headers_for("admin")).status_code == 204


def test_error_envelope_and_request_id(client):
    r = client.get("/api/v1/nodes/ghost", headers={"X-Request-ID": "req-123"})
    assert r.status_code == 404 and r.headers["X-Request-ID"] == "req-123"
    assert r.json() == {
        "error": {
            "code": "NODE_NOT_FOUND",
            "message": "node 'ghost' does not exist",
            "request_id": "req-123",
        }
    }
    assert client.get("/health").headers["X-Request-ID"]  # generated when absent


def test_validation_errors_use_envelope(client):
    r = client.post("/api/v1/deployments", json={"name": "Bad_Name", "image": "x"})
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "VALIDATION_ERROR" and err["request_id"]
    assert err["details"][0]["field"] == "name"
    assert (
        client.post(
            "/api/v1/deployments", json={"name": "ok", "image": "x", "bogus": 1}
        ).status_code
        == 422
    )


def test_unknown_route_and_health_endpoints(client):
    assert client.get("/nope").json()["error"]["code"] == "NOT_FOUND"
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_pagination_and_filtering(client):
    for i in range(5):
        client.post("/api/v1/deployments", json={"name": f"app-{i}", "image": "x"})
    page = client.get("/api/v1/deployments", params={"limit": 2, "offset": 2}).json()
    assert (
        page["total"] == 5
        and page["limit"] == 2
        and [d["name"] for d in page["items"]] == ["app-2", "app-3"]
    )
    assert client.get("/api/v1/deployments", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/pods", params={"status": "RUNNING"}).json()["total"] == 0


def test_metrics_expose_required_series(client):
    client.get("/api/v1/nodes")
    text = client.get("/metrics").text
    for name in [
        "orchestrator_requests_total",
        "orchestrator_request_duration_seconds",
        "nodes_total",
        "nodes_ready",
        "nodes_unhealthy",
        "pods_total",
        "pods_running",
        "pods_failed",
        "pods_restarts_total",
        "scheduler_attempts_total",
        "scheduler_failures_total",
        "reconciliation_total",
        "reconciliation_failures_total",
    ]:
        assert name in text or name.replace("_total", "") in text, name
    assert 'path="/api/v1/nodes"' in text  # route template, not raw path


def test_node_registration_is_idempotent_and_heartbeat_works(client):
    reg = {
        "hostname": "worker-1",
        "ip": "10.0.0.10",
        "cpu": 4,
        "memory": 8192,
        "labels": {"zone": "a"},
    }
    a = client.post("/api/v1/nodes/register", json=reg).json()
    b = client.post("/api/v1/nodes/register", json=reg).json()
    assert a["node_id"] == b["node_id"] and a["heartbeat_interval"] == 10
    assert client.get("/api/v1/nodes").json()["total"] == 1
    hb = client.post(f"/api/v1/nodes/{a['node_id']}/heartbeat", json={})
    assert hb.status_code == 200 and hb.json()["status"] == "READY"
    assert client.post("/api/v1/nodes/ghost/heartbeat", json={}).status_code == 404


def test_delete_node_requires_empty_unless_forced(cluster):
    cluster.add_worker("worker-1")
    cluster.deploy("web", 1)
    cluster.tick(3)
    r = cluster.api.delete("/api/v1/nodes/worker-1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NODE_NOT_EMPTY"
    assert cluster.api.delete("/api/v1/nodes/worker-1", params={"force": True}).status_code == 204


def test_worker_cannot_report_for_foreign_pod(cluster):
    cluster.add_worker("worker-1")
    cluster.add_worker("worker-2")
    cluster.deploy("web", 1)
    cluster.tick(3)
    pod = cluster.pods()[0]
    other = "worker-2" if pod["node"] == "worker-1" else "worker-1"
    r = cluster.workers[other].http.put(
        f"/api/v1/nodes/{other}/pods/{pod['id']}/status", json={"status": "FAILED"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "POD_NOT_ON_NODE"


def test_logs_flow_through_agent_and_error_paths(cluster, monkeypatch):
    from fastapi.testclient import TestClient

    from worker.agent.server import create_agent_app

    w = cluster.add_worker("worker-1")
    cluster.deploy("web", 1)
    cluster.tick(3)
    pod = cluster.pods()[0]
    agent = TestClient(create_agent_app(w.runtime, "worker-key"))

    def route(url, params=None, headers=None, timeout=None):
        return agent.get(url.split("9000", 1)[1], params=params, headers=headers)

    monkeypatch.setattr("orchestrator.api.routes.pods.httpx.get", route)
    r = cluster.api.get(f"/api/v1/pods/{pod['name']}/logs")
    assert r.status_code == 200 and "log output of orch-" in r.text
    assert agent.get(f"/pods/{pod['id']}/logs").status_code == 401  # agent enforces auth
    assert (
        agent.get("/pods/unknown/logs", headers={"Authorization": "Bearer worker-key"}).status_code
        == 404
    )

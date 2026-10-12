"""Concurrency and duplicate-request behaviour against real PostgreSQL row locks / constraints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from orchestrator.persistence.models import Deployment, IdempotencyKey
from tests.conftest import headers_for

BODY = {"name": "web", "image": "nginx", "replicas": 2}


def _fresh_client(app):
    return TestClient(app, headers=headers_for("admin"))


def test_idempotency_key_replays_stored_response(client, db):
    h = {"Idempotency-Key": "abc-1"}
    first = client.post("/api/v1/deployments", json=BODY, headers=h)
    second = client.post("/api/v1/deployments", json=BODY, headers=h)
    assert first.status_code == 201 and second.status_code == 201  # replayed, not 200
    assert second.headers["Idempotent-Replayed"] == "true" and first.json() == second.json()
    with db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(Deployment)) == 1


def test_idempotency_key_reuse_with_different_body_is_rejected(client):
    h = {"Idempotency-Key": "abc-2"}
    client.post("/api/v1/deployments", json=BODY, headers=h)
    r = client.post("/api/v1/deployments", json={**BODY, "replicas": 9}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_failed_request_does_not_burn_the_key(client, db):
    h = {"Idempotency-Key": "abc-3"}
    assert (
        client.post("/api/v1/deployments/ghost/scale", json={"replicas": 1}, headers=h).status_code
        == 404
    )
    client.post("/api/v1/deployments", json=BODY)
    assert (
        client.post("/api/v1/deployments/web/scale", json={"replicas": 1}, headers=h).status_code
        == 200
    )
    with db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(IdempotencyKey)) == 1


def test_duplicate_create_without_key_is_safe(client):
    assert client.post("/api/v1/deployments", json=BODY).status_code == 201
    assert client.post("/api/v1/deployments", json=BODY).status_code == 200  # same spec: no-op
    r = client.post("/api/v1/deployments", json={**BODY, "image": "other"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEPLOYMENT_ALREADY_EXISTS"


def test_concurrent_identical_creates_produce_one_deployment(app, client, db):
    def create(_):
        c = _fresh_client(app)
        return c.post("/api/v1/deployments", json=BODY).status_code

    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(create, range(8)))
    assert sorted(set(codes)) in ([200, 201], [201])
    assert codes.count(201) == 1
    with db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(Deployment)) == 1


def test_concurrent_creates_with_same_idempotency_key_execute_once(app, client, db):
    def create(_):
        c = _fresh_client(app)
        r = c.post("/api/v1/deployments", json=BODY, headers={"Idempotency-Key": "race"})
        return r.status_code, r.headers.get("Idempotent-Replayed")

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(create, range(8)))
    assert {code for code, _ in results} == {201}
    assert sum(1 for _, replayed in results if replayed is None) == 1
    with db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(Deployment)) == 1


def test_rapid_concurrent_scaling_converges_to_a_single_requested_value(cluster, app):
    cluster.add_worker("worker-1", cpu=16, memory=16384)
    cluster.post(
        "/api/v1/deployments",
        {"name": "web", "image": "nginx", "replicas": 1, "cpu_request": 0.1, "memory_request": 64},
        expect=201,
    )
    targets = [5, 5, 3, 5, 8, 5, 5, 5]

    def scale(n):
        c = _fresh_client(app)
        return c.post("/api/v1/deployments/web/scale", json={"replicas": n}).status_code

    with ThreadPoolExecutor(8) as pool:
        assert set(pool.map(scale, targets)) == {200}
    final = cluster.get("/api/v1/deployments/web")["desired_replicas"]
    assert final in {3, 5, 8}
    cluster.tick(6)
    pods = cluster.pods()
    assert len(pods) == final and len({p["name"] for p in pods}) == final  # no duplicates


def test_concurrent_schedulers_never_overcommit_a_node(cluster, db, settings):
    """Two scheduler passes racing for the same capacity: the node row lock + re-check holds."""
    from orchestrator.scheduler.service import schedule_pending
    from orchestrator.scheduler.strategies import get_strategy

    cluster.add_worker("worker-1", cpu=1, memory=1024)
    cluster.post(
        "/api/v1/deployments",
        {
            "name": "web",
            "image": "nginx",
            "replicas": 10,
            "cpu_request": 0.25,
            "memory_request": 64,
        },
        expect=201,
    )
    from orchestrator.controller.deployments import reconcile_deployments

    reconcile_deployments(db, settings)
    strategy = get_strategy("least_loaded")
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda _: schedule_pending(db, strategy), range(6)))
    node = cluster.get("/api/v1/nodes/worker-1")
    assert node["cpu_allocated"] == 1.0 and node["pod_count"] == 4  # capacity for exactly 4
    assert len(cluster.pods(status="SCHEDULED")) == 4 and len(cluster.pods(status="PENDING")) == 6

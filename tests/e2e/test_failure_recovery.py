"""Failure scenarios: container crash, worker death, unhealthy pods, drain, bad images."""

from __future__ import annotations


def _events(cluster, type_):
    return cluster.get("/api/v1/events", params={"type": type_})["items"]


def _force_checks(worker, times):
    """Make the agent's interval-gated health checks due again, then run one tick each time."""
    for _ in range(times):
        for state in worker.agent._health.values():
            state.last_checked = 0.0
        worker.cluster.tick(1)


def _setup(cluster, replicas=3, workers=("worker-1", "worker-2")):
    for name in workers:
        cluster.add_worker(name)
    cluster.post(
        "/api/v1/services",
        {"name": "web", "selector": {"app": "web"}, "port": 80, "target_port": 80},
    )
    cluster.deploy("web", replicas)
    cluster.tick(4)


def test_container_crash_is_restarted_and_endpoint_returns(cluster):
    _setup(cluster)
    w1 = cluster.workers["worker-1"]
    victim = w1.runtime.running()[0]
    w1.runtime.kill(victim, exit_code=1)

    cluster.tick(1)  # agent reports FAILED; the pod controller restarts it in the same pass
    cluster.tick(3)
    pods = cluster.pods()
    assert len(pods) == 3 and all(p["status"] == "RUNNING" for p in pods)
    assert sum(p["restart_count"] for p in pods) == 1
    assert len(w1.runtime.running()) == 2 and victim not in w1.runtime.containers  # fresh container
    assert len(cluster.get("/api/v1/services/web")["endpoints"]) == 3
    assert _events(cluster, "POD_FAILED") and _events(cluster, "POD_RESTARTED")


def test_restart_policy_never_leaves_pod_failed_and_does_not_replace_it(cluster):
    cluster.add_worker("worker-1")
    cluster.deploy("once", 1, restart_policy="Never")
    cluster.tick(3)
    w1 = cluster.workers["worker-1"]
    w1.runtime.kill_all(exit_code=1)
    cluster.tick(4)
    pods = cluster.pods()
    assert [p["status"] for p in pods] == ["FAILED"] and pods[0]["restart_count"] == 0


def test_worker_failure_reschedules_workloads(cluster):
    _setup(cluster)
    on_w1 = [p for p in cluster.pods() if p["node"] == "worker-1"]
    assert on_w1
    cluster.stop_worker("worker-1")
    cluster.expire_heartbeat("worker-1", 45)
    cluster.tick(1)
    assert cluster.get("/api/v1/nodes/worker-1")["status"] == "UNHEALTHY"
    cluster.expire_heartbeat("worker-1", 90)
    cluster.tick(5)

    assert cluster.get("/api/v1/nodes/worker-1")["status"] == "OFFLINE"
    running = cluster.pods(status="RUNNING")
    assert len(running) == 3 and {p["node"] for p in running} == {"worker-2"}
    assert len(cluster.get("/api/v1/services/web")["endpoints"]) == 3
    assert _events(cluster, "NODE_UNHEALTHY") and _events(cluster, "NODE_OFFLINE")
    lost = cluster.get("/api/v1/pods", params={"status": "TERMINATED"})["items"]
    assert {p["reason"] for p in lost} == {"NodeLost"}


def test_returning_worker_cleans_up_orphaned_containers(cluster):
    """A partitioned worker keeps its containers; when it reconnects it must remove them
    because the control plane already replaced those pods."""
    _setup(cluster)
    w1 = cluster.workers["worker-1"]
    stale = set(w1.runtime.containers)
    w1.connected = False  # network partition: agent alive but cut off from the API
    cluster.expire_heartbeat("worker-1", 120)
    cluster.tick(5)
    assert len(cluster.pods(status="RUNNING")) == 3
    assert set(w1.runtime.containers) == stale  # zombies still there during the partition

    w1.connected = True
    cluster.tick(4)
    assert cluster.get("/api/v1/nodes/worker-1")["status"] == "READY"
    assert not (set(w1.runtime.containers) & stale)
    assert len(cluster.pods(status="RUNNING")) == 3


def test_unhealthy_pod_leaves_service_and_recovers(cluster):
    _setup(cluster, replicas=2, workers=("worker-1",))
    w1 = cluster.workers["worker-1"]
    w1.cluster = cluster
    bad_port = next(iter(w1.runtime.containers.values()))["ports"][80]
    w1.health.down.add(bad_port)
    _force_checks(w1, 3)  # failure_threshold is 2
    statuses = sorted(p["status"] for p in cluster.pods())
    assert statuses == ["RUNNING", "UNHEALTHY"]
    assert len(cluster.get("/api/v1/services/web")["endpoints"]) == 1

    w1.health.down.clear()
    _force_checks(w1, 2)
    assert sorted(p["status"] for p in cluster.pods()) == ["RUNNING", "RUNNING"]
    assert len(cluster.get("/api/v1/services/web")["endpoints"]) == 2


def test_drain_moves_workloads_and_completes(cluster):
    _setup(cluster, replicas=4)
    r = cluster.post("/api/v1/nodes/worker-1/drain")
    assert r.status_code == 200 and r.json()["status"] == "DRAINING"
    cluster.tick(6)
    w1 = cluster.workers["worker-1"]
    assert w1.runtime.containers == {}
    running = cluster.pods(status="RUNNING")
    assert len(running) == 4 and {p["node"] for p in running} == {"worker-2"}
    assert cluster.get("/api/v1/nodes/worker-1")["drain_completed_at"] is not None
    assert _events(cluster, "NODE_DRAINED")

    cluster.post("/api/v1/nodes/worker-1/uncordon", expect=200)
    assert cluster.get("/api/v1/nodes/worker-1")["status"] == "READY"


def test_invalid_image_fails_permanently_without_churn(cluster):
    cluster.add_worker("worker-1")
    cluster.post(
        "/api/v1/deployments", {"name": "bad", "image": "invalid/nope:1", "replicas": 2}, expect=201
    )
    cluster.tick(8)
    pods = cluster.pods()
    assert len(pods) == 2  # not endlessly replaced
    assert {p["status"] for p in pods} == {"FAILED"}
    assert {p["reason"] for p in pods} == {"ImageNotFound"}
    assert all(p["restart_count"] == 0 for p in pods)
    assert cluster.get("/api/v1/nodes/worker-1")["pod_count"] == 0  # capacity released


def test_deleted_pod_is_replaced_by_its_deployment(cluster):
    _setup(cluster)
    victim = cluster.pods()[0]
    assert cluster.api.delete(f"/api/v1/pods/{victim['name']}").status_code == 202
    cluster.tick(5)
    running = cluster.pods(status="RUNNING")
    assert len(running) == 3 and victim["name"] not in {p["name"] for p in running}

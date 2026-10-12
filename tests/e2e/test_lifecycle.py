"""Create deployment -> schedule -> container -> health -> endpoint, then scaling."""

from __future__ import annotations


def _service(cluster, name="web"):
    return cluster.post(
        "/api/v1/services", {"name": name, "selector": {"app": name}, "port": 80, "target_port": 80}
    )


def test_full_deployment_flow_registers_endpoints(cluster):
    cluster.add_worker("worker-1")
    cluster.add_worker("worker-2")
    assert _service(cluster).status_code == 201
    assert cluster.deploy("web", 3).status_code == 201
    cluster.tick(4)

    pods = cluster.pods()
    assert len(pods) == 3
    assert all(p["status"] == "RUNNING" and p["ready"] for p in pods)
    assert cluster.running_by_node() == {"worker-1": 2, "worker-2": 1}  # least-loaded spreads
    svc = cluster.get("/api/v1/services/web")
    assert len(svc["endpoints"]) == 3
    assert {e["address"] for e in svc["endpoints"]} == {"worker-1", "worker-2"}
    dep = cluster.get("/api/v1/deployments/web")
    assert (dep["desired_replicas"], dep["ready_replicas"]) == (3, 3)


def test_real_containers_were_created_by_the_workers(cluster):
    w1, w2 = cluster.add_worker("worker-1"), cluster.add_worker("worker-2")
    cluster.deploy("web", 3)
    cluster.tick(4)
    assert len(w1.runtime.running()) + len(w2.runtime.running()) == 3
    spec = next(iter(w1.runtime.containers.values()))["spec"]
    assert spec.image == "nginx:alpine" and spec.cpu == 0.25 and spec.memory_mb == 256


def test_resource_accounting_tracks_allocations(cluster):
    cluster.add_worker("worker-1", cpu=2, memory=1024)
    cluster.deploy("web", 3)  # 3 x (0.25 cpu, 256 MiB)
    cluster.tick(4)
    node = cluster.get("/api/v1/nodes/worker-1")
    assert node["cpu_allocated"] == 0.75 and node["memory_allocated_mb"] == 768
    assert node["pod_count"] == 3


def test_scale_up_and_down_converges(cluster):
    cluster.add_worker("worker-1")
    cluster.add_worker("worker-2")
    cluster.deploy("web", 3)
    cluster.tick(4)
    assert cluster.post("/api/v1/deployments/web/scale", {"replicas": 5}, expect=200)
    cluster.tick(4)
    assert len(cluster.pods(status="RUNNING")) == 5
    cluster.post("/api/v1/deployments/web/scale", {"replicas": 2}, expect=200)
    cluster.tick(5)
    assert len(cluster.pods()) == 2
    assert sum(len(w.runtime.containers) for w in cluster.workers.values()) == 2
    node = cluster.get("/api/v1/nodes")["items"]
    assert sum(n["pod_count"] for n in node) == 2  # allocations were released


def test_scale_is_idempotent_and_to_zero_works(cluster):
    cluster.add_worker("worker-1")
    cluster.deploy("web", 2)
    cluster.tick(3)
    for _ in range(3):
        assert cluster.post("/api/v1/deployments/web/scale", {"replicas": 2}).status_code == 200
    assert cluster.get("/api/v1/deployments/web")["generation"] == 1
    cluster.post("/api/v1/deployments/web/scale", {"replicas": 0}, expect=200)
    cluster.tick(4)
    assert cluster.pods() == []


def test_delete_deployment_removes_pods_and_containers(cluster):
    w1 = cluster.add_worker("worker-1")
    cluster.deploy("web", 2)
    cluster.tick(3)
    assert cluster.api.delete("/api/v1/deployments/web").status_code == 202
    cluster.tick(5)
    assert cluster.api.get("/api/v1/deployments/web").status_code == 404
    assert w1.runtime.containers == {}
    assert cluster.get("/api/v1/nodes/worker-1")["pod_count"] == 0


def test_service_resolve_round_robins_over_ready_endpoints(cluster):
    cluster.add_worker("worker-1")
    cluster.add_worker("worker-2")
    _service(cluster)
    cluster.deploy("web", 3)
    cluster.tick(4)
    picks = [cluster.get("/api/v1/services/web/resolve")["pod"] for _ in range(6)]
    assert len(set(picks)) == 3 and picks[:3] == picks[3:]


def test_resolve_returns_503_without_endpoints(cluster):
    _service(cluster)
    r = cluster.api.get("/api/v1/services/web/resolve")
    assert r.status_code == 503 and r.json()["error"]["code"] == "NO_READY_ENDPOINTS"


def test_pod_without_health_check_is_ready_when_running(cluster):
    cluster.add_worker("worker-1")
    cluster.post(
        "/api/v1/deployments",
        {"name": "job", "image": "busybox", "replicas": 1, "ports": [{"container_port": 8080}]},
        expect=201,
    )
    cluster.tick(3)
    pod = cluster.pods()[0]
    assert pod["status"] == "RUNNING" and pod["ready"]

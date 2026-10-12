from __future__ import annotations


def test_insufficient_resources_leave_pods_pending_with_reason(cluster):
    cluster.add_worker("worker-1", cpu=1, memory=1024)
    cluster.deploy("big", 2, cpu_request=0.75)  # only one fits
    cluster.tick(4)
    pods = cluster.pods()
    assert sorted(p["status"] for p in pods) == ["PENDING", "RUNNING"]
    pending = next(p for p in pods if p["status"] == "PENDING")
    assert "insufficient cpu" in pending["message"] and pending["reason"] == "Unschedulable"
    events = cluster.get("/api/v1/events", params={"type": "POD_UNSCHEDULABLE"})["items"]
    assert len(events) == 1  # message is stable, so no event spam across ticks

    cluster.add_worker("worker-2", cpu=1, memory=1024)  # capacity arrives -> pod schedules
    cluster.tick(4)
    assert sorted(p["status"] for p in cluster.pods()) == ["RUNNING", "RUNNING"]


def test_more_workloads_than_cluster_capacity_never_overcommits(cluster):
    cluster.add_worker("worker-1", cpu=1, memory=512)
    cluster.add_worker("worker-2", cpu=1, memory=512)
    cluster.deploy("many", 20, cpu_request=0.25, memory_request=128)  # capacity for 8
    cluster.tick(6)
    by_status = {}
    for p in cluster.pods():
        by_status[p["status"]] = by_status.get(p["status"], 0) + 1
    assert by_status == {"RUNNING": 8, "PENDING": 12}
    for n in cluster.get("/api/v1/nodes")["items"]:
        assert n["cpu_allocated"] <= n["cpu_capacity"]
        assert n["memory_allocated_mb"] <= n["memory_capacity_mb"]


def test_node_selector_restricts_placement(cluster):
    cluster.add_worker("worker-1", labels={"zone": "a"})
    cluster.add_worker("worker-2", labels={"zone": "b"})
    cluster.deploy("pinned", 3, node_selector={"zone": "b"})
    cluster.tick(4)
    assert {p["node"] for p in cluster.pods(status="RUNNING")} == {"worker-2"}


def test_cordoned_or_offline_nodes_receive_no_pods(cluster):
    cluster.add_worker("worker-1")
    cluster.add_worker("worker-2")
    cluster.post("/api/v1/nodes/worker-1/drain", expect=200)
    cluster.deploy("web", 3)
    cluster.tick(4)
    assert {p["node"] for p in cluster.pods(status="RUNNING")} == {"worker-2"}


def test_first_fit_packs_onto_first_node(cluster, settings):
    from orchestrator.scheduler.strategies import get_strategy

    cluster.strategy = get_strategy("first_fit")
    cluster.add_worker("worker-1", cpu=4)
    cluster.add_worker("worker-2", cpu=4)
    cluster.deploy("web", 3)
    cluster.tick(4)
    assert cluster.running_by_node() == {"worker-1": 3}

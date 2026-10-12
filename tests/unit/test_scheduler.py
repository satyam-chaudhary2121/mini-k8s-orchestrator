import pytest

from orchestrator.scheduler.strategies import (
    FirstFitStrategy,
    LeastLoadedStrategy,
    NodeView,
    PodRequest,
    ResourceAwareStrategy,
    filter_nodes,
    get_strategy,
)


def node(
    name, cpu=4.0, mem=4096, cpu_a=0.0, mem_a=0, pods=0, status="READY", cordoned=False, labels=None
):
    return NodeView(
        id=name,
        hostname=name,
        status=status,
        cordoned=cordoned,
        labels=labels or {},
        cpu_capacity=cpu,
        memory_capacity_mb=mem,
        cpu_allocated=cpu_a,
        memory_allocated_mb=mem_a,
        pod_count=pods,
    )


REQ = PodRequest(cpu=1.0, memory_mb=512)


def test_filter_rejects_unready_cordoned_and_full_nodes():
    nodes = [
        node("ok"),
        node("off", status="OFFLINE"),
        node("sick", status="UNHEALTHY"),
        node("drain", status="DRAINING", cordoned=True),
        node("full-cpu", cpu_a=3.5),
        node("full-mem", mem_a=4000),
    ]
    result = filter_nodes(nodes, REQ)
    assert [n.hostname for n in result.candidates] == ["ok"]
    assert (
        result.rejections["insufficient cpu"] == 1 and result.rejections["insufficient memory"] == 1
    )
    assert "0/6" in result.explain(6) or "nodes are available" in result.explain(6)


def test_filter_honours_node_selector():
    nodes = [node("a", labels={"zone": "a"}), node("b", labels={"zone": "b", "disk": "ssd"})]
    req = PodRequest(1, 100, {"zone": "b", "disk": "ssd"})
    assert [n.hostname for n in filter_nodes(nodes, req).candidates] == ["b"]
    assert filter_nodes(nodes, PodRequest(1, 100, {"zone": "c"})).candidates == []


def test_exact_fit_is_allowed():
    assert filter_nodes([node("a", cpu=1.0, mem=512)], REQ).candidates


def test_first_fit_is_deterministic_by_hostname():
    chosen = FirstFitStrategy().select([node("w2"), node("w1"), node("w3")], REQ)
    assert chosen.hostname == "w1"


def test_least_loaded_prefers_emptier_node():
    chosen = LeastLoadedStrategy().select(
        [node("busy", cpu_a=2, mem_a=2048, pods=4), node("idle", pods=0)], REQ
    )
    assert chosen.hostname == "idle"


def test_least_loaded_breaks_ties_by_pod_count():
    chosen = LeastLoadedStrategy().select([node("a", pods=3), node("b", pods=1)], REQ)
    assert chosen.hostname == "b"


def test_resource_aware_prefers_balanced_free_resources():
    cpu_heavy = node("cpu-skewed", cpu_a=3.0, mem_a=0)  # little cpu left, lots of memory
    balanced = node("balanced", cpu_a=1.5, mem_a=1500)
    assert (
        ResourceAwareStrategy().select([cpu_heavy, balanced], PodRequest(0.5, 256)).hostname
        == "balanced"
    )


def test_strategies_return_none_without_candidates():
    for strategy in (FirstFitStrategy(), LeastLoadedStrategy(), ResourceAwareStrategy()):
        assert strategy.select([], REQ) is None


def test_registry_and_unknown_strategy():
    assert get_strategy("first_fit").name == "first_fit"
    with pytest.raises(ValueError, match="unknown scheduler strategy"):
        get_strategy("nope")

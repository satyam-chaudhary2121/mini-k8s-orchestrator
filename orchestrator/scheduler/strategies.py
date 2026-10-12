"""Pure scheduling logic: filtering and pluggable scoring strategies (no database access)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class NodeView:
    id: str
    hostname: str
    status: str
    cordoned: bool
    labels: dict[str, str]
    cpu_capacity: float
    memory_capacity_mb: int
    cpu_allocated: float = 0.0
    memory_allocated_mb: int = 0
    pod_count: int = 0

    @property
    def cpu_free(self) -> float:
        return self.cpu_capacity - self.cpu_allocated

    @property
    def memory_free_mb(self) -> int:
        return self.memory_capacity_mb - self.memory_allocated_mb


@dataclass(frozen=True)
class PodRequest:
    cpu: float
    memory_mb: int
    node_selector: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FilterResult:
    candidates: list[NodeView]
    rejections: Counter[str]

    def explain(self, total: int) -> str:
        if not total:
            return "no nodes are registered"
        detail = ", ".join(f"{n} {why}" for why, n in sorted(self.rejections.items()))
        return f"0/{total} nodes are available: {detail}"


def filter_nodes(nodes: Sequence[NodeView], pod: PodRequest) -> FilterResult:
    """Reject nodes that cannot host the pod, recording why (used in the Unschedulable message)."""
    candidates: list[NodeView] = []
    rejections: Counter[str] = Counter()
    for node in nodes:
        if node.status != "READY" or node.cordoned:
            rejections[f"not schedulable ({node.status.lower()})"] += 1
        elif any(node.labels.get(k) != v for k, v in pod.node_selector.items()):
            rejections["didn't match node selector"] += 1
        elif node.cpu_free < pod.cpu:
            rejections["insufficient cpu"] += 1
        elif node.memory_free_mb < pod.memory_mb:
            rejections["insufficient memory"] += 1
        else:
            candidates.append(node)
    return FilterResult(candidates, rejections)


def _utilisation_after(node: NodeView, pod: PodRequest) -> tuple[float, float]:
    return (
        (node.cpu_allocated + pod.cpu) / node.cpu_capacity,
        (node.memory_allocated_mb + pod.memory_mb) / node.memory_capacity_mb,
    )


class SchedulerStrategy(ABC):
    """Chooses one node among candidates that already passed filtering."""

    name: str

    @abstractmethod
    def select(self, candidates: Sequence[NodeView], pod: PodRequest) -> NodeView | None: ...


class FirstFitStrategy(SchedulerStrategy):
    """First node (by hostname) that fits. Cheap and deterministic; tends to pack nodes."""

    name = "first_fit"

    def select(self, candidates: Sequence[NodeView], pod: PodRequest) -> NodeView | None:
        return min(candidates, key=lambda n: n.hostname, default=None)


class LeastLoadedStrategy(SchedulerStrategy):
    """Lowest mean CPU/memory utilisation after placement; ties broken by pod count."""

    name = "least_loaded"

    def select(self, candidates: Sequence[NodeView], pod: PodRequest) -> NodeView | None:
        def key(node: NodeView) -> tuple[float, int, str]:
            cpu, mem = _utilisation_after(node, pod)
            return ((cpu + mem) / 2, node.pod_count, node.hostname)

        return min(candidates, key=key, default=None)


class ResourceAwareStrategy(SchedulerStrategy):
    """Maximise the scarcer free resource and keep CPU/memory use balanced (anti-fragmentation)."""

    name = "resource_aware"

    def select(self, candidates: Sequence[NodeView], pod: PodRequest) -> NodeView | None:
        def key(node: NodeView) -> tuple[float, int, str]:
            cpu, mem = _utilisation_after(node, pod)
            free_cpu, free_mem = 1 - cpu, 1 - mem
            score = min(free_cpu, free_mem) - 0.5 * abs(free_cpu - free_mem)
            return (-score, node.pod_count, node.hostname)

        return min(candidates, key=key, default=None)


STRATEGIES: dict[str, type[SchedulerStrategy]] = {
    cls.name: cls for cls in (FirstFitStrategy, LeastLoadedStrategy, ResourceAwareStrategy)
}


def get_strategy(name: str) -> SchedulerStrategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise ValueError(
            f"unknown scheduler strategy {name!r}; choose from {sorted(STRATEGIES)}"
        ) from None

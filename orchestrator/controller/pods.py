from __future__ import annotations

from datetime import timedelta
from typing import Literal

from sqlalchemy import delete, or_, select

from orchestrator import metrics
from orchestrator.config.settings import Settings
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.db import Database
from orchestrator.persistence.models import (
    DesiredState,
    Node,
    NodeStatus,
    Pod,
    PodStatus,
    utcnow,
)
from orchestrator.services.pods import (
    ACTIVE_STATUSES,
    release_allocation,
    seconds_since,
    transition_pod,
)

Decision = Literal["restart", "wait", "stay_failed"]


def is_restartable(policy: str, exit_code: int | None, permanent: bool) -> bool:
    if permanent:
        return False
    if policy == "Always":
        return True
    if policy == "OnFailure":
        return exit_code != 0
    return False


def restart_delay(restart_count: int, base: float, maximum: float) -> float:
    """CrashLoopBackOff-style delay: base * 2^restarts, capped."""
    return min(maximum, base * 2**restart_count)


def decide_restart(pod: Pod, settings: Settings) -> Decision:
    if not is_restartable(pod.restart_policy, pod.exit_code, pod.permanent_failure):
        return "stay_failed"
    delay = restart_delay(
        pod.restart_count, settings.restart_backoff_base, settings.restart_backoff_max
    )
    return "restart" if seconds_since(pod.last_transition_at) >= delay else "wait"


def reconcile_pods(db: Database, settings: Settings) -> None:
    """Drive pods toward their desired_state: terminate what should stop, restart what failed
    (with backoff), and prune old terminated records."""
    with db.transaction() as session:
        ids = list(
            session.scalars(
                select(Pod.id).where(
                    or_(
                        (Pod.desired_state == DesiredState.TERMINATED.value)
                        & (Pod.status != PodStatus.TERMINATED.value),
                        (Pod.desired_state == DesiredState.RUNNING.value)
                        & (Pod.status == PodStatus.FAILED.value),
                    )
                )
            )
        )
    for pod_id in ids:
        with db.transaction() as session:
            pod = session.scalars(
                select(Pod).where(Pod.id == pod_id).with_for_update(skip_locked=True)
            ).first()
            if pod is None:
                continue
            node = session.get(Node, pod.node_id) if pod.node_id else None
            if pod.desired_state == DesiredState.TERMINATED.value:
                _terminate(session, pod, node)
            elif pod.status == PodStatus.FAILED.value:
                _handle_failed(session, pod, settings)
    with db.transaction() as session:
        cutoff = utcnow() - timedelta(seconds=settings.pod_retention_seconds)
        session.execute(
            delete(Pod).where(
                Pod.status == PodStatus.TERMINATED.value, Pod.last_transition_at < cutoff
            )
        )


def _terminate(session, pod: Pod, node: Node | None) -> None:  # type: ignore[no-untyped-def]
    status = PodStatus(pod.status)
    node_gone = node is None or node.status == NodeStatus.OFFLINE.value
    if status in (PodStatus.PENDING, PodStatus.FAILED):
        transition_pod(session, pod, PodStatus.TERMINATED, reason=pod.reason)
    elif status in ACTIVE_STATUSES:
        transition_pod(session, pod, PodStatus.TERMINATING)
        if node_gone:
            transition_pod(session, pod, PodStatus.TERMINATED)
    elif status is PodStatus.TERMINATING and node_gone:
        transition_pod(session, pod, PodStatus.TERMINATED)


def _handle_failed(session, pod: Pod, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    decision = decide_restart(pod, settings)
    if decision == "restart":
        pod.restart_count += 1
        pod.restart_epoch += 1  # tells the worker to create a fresh container
        pod.port_map = {}
        transition_pod(
            session,
            pod,
            PodStatus.SCHEDULED,
            reason="Restarting",
            message=f"restart #{pod.restart_count} after {pod.reason}",
        )
        emit(
            session,
            EventType.POD_RESTARTED,
            "pod",
            pod.id,
            f"pod {pod.name} restarted (#{pod.restart_count})",
            pod=pod.name,
        )
        metrics.POD_RESTARTS.inc()
    elif decision == "stay_failed":
        release_allocation(session, pod)  # a failed pod that will not restart holds no capacity

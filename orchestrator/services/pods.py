"""Pod state machine and resource-allocation helpers shared by API and controllers."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from orchestrator.common.errors import InvalidTransitionError
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.models import Pod, PodStatus, ResourceAllocation, utcnow

S = PodStatus

POD_TRANSITIONS: dict[PodStatus, frozenset[PodStatus]] = {
    S.PENDING: frozenset({S.SCHEDULED, S.TERMINATED}),
    S.SCHEDULED: frozenset({S.CREATING, S.FAILED, S.TERMINATING}),
    S.CREATING: frozenset({S.RUNNING, S.FAILED, S.TERMINATING}),
    S.RUNNING: frozenset({S.UNHEALTHY, S.FAILED, S.TERMINATING}),
    S.UNHEALTHY: frozenset({S.RUNNING, S.FAILED, S.TERMINATING}),
    S.FAILED: frozenset({S.SCHEDULED, S.TERMINATED}),
    S.TERMINATING: frozenset({S.TERMINATED}),
    S.TERMINATED: frozenset(),
}

ACTIVE_STATUSES = (S.SCHEDULED, S.CREATING, S.RUNNING, S.UNHEALTHY)
LIVE_STATUSES = (S.PENDING, *ACTIVE_STATUSES, S.FAILED)

_TRANSITION_EVENTS = {
    S.SCHEDULED: EventType.POD_SCHEDULED,
    S.RUNNING: EventType.POD_STARTED,
    S.UNHEALTHY: EventType.POD_UNHEALTHY,
    S.FAILED: EventType.POD_FAILED,
    S.TERMINATED: EventType.POD_DELETED,
}


def can_transition(current: PodStatus, new: PodStatus) -> bool:
    return new in POD_TRANSITIONS[current]


def transition_pod(
    session: Session,
    pod: Pod,
    new: PodStatus,
    *,
    reason: str | None = None,
    message: str | None = None,
) -> None:
    """Move a pod to ``new`` if the state machine allows it; raise otherwise."""
    current = PodStatus(pod.status)
    if not can_transition(current, new):
        raise InvalidTransitionError(
            f"pod {pod.name}: {current.value} -> {new.value} is not allowed"
        )
    pod.status = new.value
    pod.reason = reason
    pod.message = message
    pod.last_transition_at = utcnow()
    if new is S.TERMINATED:
        pod.ready = False
        release_allocation(session, pod)
    if new in (S.FAILED, S.TERMINATING, S.UNHEALTHY):
        pod.ready = False
    event = _TRANSITION_EVENTS.get(new)
    if event:
        emit(
            session,
            event,
            "pod",
            pod.id,
            f"pod {pod.name} -> {new.value}",
            pod=pod.name,
            node_id=pod.node_id,
            reason=reason,
        )


def release_allocation(session: Session, pod: Pod) -> None:
    session.execute(delete(ResourceAllocation).where(ResourceAllocation.pod_id == pod.id))


def seconds_since(moment: datetime) -> float:
    return (utcnow() - moment).total_seconds()

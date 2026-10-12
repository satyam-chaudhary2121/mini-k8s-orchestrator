"""Applying worker-reported pod status to the persisted pod, via the state machine."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts.schemas import PodStatusReport
from orchestrator.persistence.models import Container, HealthCheckState, Pod, PodStatus, utcnow
from orchestrator.services.pods import transition_pod

S = PodStatus


def assignment_for(pod: Pod) -> dict[str, Any]:
    return {
        "id": pod.id,
        "name": pod.name,
        "status": pod.status,
        "desired_state": pod.desired_state,
        "restart_epoch": pod.restart_epoch,
        "ready": pod.ready,
        "reason": pod.reason,
        "labels": pod.labels,
        "spec": pod.spec,
    }


def _record_container(session: Session, pod: Pod, report: PodStatusReport) -> None:
    if not report.container_id:
        return
    row = session.scalars(
        select(Container).where(Container.pod_id == pod.id, Container.epoch == pod.restart_epoch)
    ).first()
    if row is None:
        row = Container(
            pod_id=pod.id,
            epoch=pod.restart_epoch,
            runtime_id=report.container_id,
            image=pod.spec.get("image", ""),
            status=report.status,
            started_at=utcnow(),
        )
        session.add(row)
    row.status, row.exit_code = report.status, report.exit_code
    if report.status in ("FAILED", "TERMINATED"):
        row.finished_at = utcnow()


def _record_health(session: Session, pod: Pod, report: PodStatusReport) -> None:
    hc = pod.spec.get("health_check")
    if not hc or report.healthy is None:
        return
    state = session.scalars(
        select(HealthCheckState).where(HealthCheckState.pod_id == pod.id)
    ).first()
    if state is None:
        state = HealthCheckState(pod_id=pod.id, type=hc["type"], spec=hc)
        session.add(state)
    state.last_result = report.healthy
    state.consecutive_failures = report.consecutive_failures
    state.last_checked_at = utcnow()


def apply_status_report(session: Session, pod: Pod, report: PodStatusReport) -> None:
    """Idempotent: reports that do not advance the state machine are ignored, not errors."""
    status = S(pod.status)
    if status is S.TERMINATED:
        return
    _record_container(session, pod, report)

    if report.status == "CREATING":
        if status is S.SCHEDULED:
            transition_pod(session, pod, S.CREATING)
    elif report.status == "RUNNING":
        if status is S.SCHEDULED:
            transition_pod(session, pod, S.CREATING)
            status = S.CREATING
        if status is S.CREATING:
            transition_pod(session, pod, S.RUNNING)
        if S(pod.status) in (S.RUNNING, S.UNHEALTHY):
            pod.port_map = report.ports
            _apply_health(session, pod, report)
    elif report.status == "FAILED":
        if status in (S.SCHEDULED, S.CREATING, S.RUNNING, S.UNHEALTHY):
            pod.exit_code = report.exit_code
            pod.permanent_failure = report.permanent
            transition_pod(session, pod, S.FAILED, reason=report.reason, message=report.message)
    elif report.status == "TERMINATED":
        if status in (S.SCHEDULED, S.CREATING, S.RUNNING, S.UNHEALTHY):
            transition_pod(session, pod, S.TERMINATING)
        if S(pod.status) is S.TERMINATING:
            transition_pod(session, pod, S.TERMINATED)


def _apply_health(session: Session, pod: Pod, report: PodStatusReport) -> None:
    hc = pod.spec.get("health_check")
    if not hc:
        pod.ready = True
        return
    _record_health(session, pod, report)
    threshold = hc.get("failure_threshold", 3)
    if S(pod.status) is S.RUNNING and report.consecutive_failures >= threshold:
        transition_pod(
            session,
            pod,
            S.UNHEALTHY,
            reason="HealthCheckFailed",
            message=f"{report.consecutive_failures} consecutive failures",
        )
    elif S(pod.status) is S.UNHEALTHY and report.healthy:
        transition_pod(session, pod, S.RUNNING)
    pod.ready = bool(report.healthy) and S(pod.status) is S.RUNNING

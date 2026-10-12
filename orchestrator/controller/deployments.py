from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from orchestrator.config.settings import Settings
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.db import Database
from orchestrator.persistence.models import (
    Deployment,
    DeploymentStatus,
    DesiredState,
    Pod,
    PodStatus,
)
from orchestrator.services.deployments import new_pod_name

MAX_CREATES_PER_PASS = 50
_VICTIM_RANK = {
    PodStatus.FAILED: 0,
    PodStatus.PENDING: 1,
    PodStatus.SCHEDULED: 2,
    PodStatus.CREATING: 3,
    PodStatus.UNHEALTHY: 4,
    PodStatus.TERMINATING: 5,
    PodStatus.RUNNING: 6,
}


def pick_victims(pods: list[Pod], count: int) -> list[Pod]:
    """Scale-down order: least valuable first, newest first within a tier."""
    ranked = sorted(
        pods, key=lambda p: (_VICTIM_RANK[PodStatus(p.status)], -p.created_at.timestamp())
    )
    return ranked[:count]


def build_pod(dep: Deployment) -> Pod:
    template = dep.template
    return Pod(
        name=new_pod_name(dep.name),
        deployment_id=dep.id,
        spec=template,
        labels={"app": dep.name, **template.get("labels", {}), "deployment": dep.name},
        cpu_request=template["cpu_request"],
        memory_request_mb=template["memory_request"],
        restart_policy=template.get("restart_policy", "Always"),
    )


def reconcile_deployment(session: Session, dep: Deployment) -> None:
    pods = list(
        session.scalars(
            select(Pod)
            .where(Pod.deployment_id == dep.id, Pod.status != PodStatus.TERMINATED.value)
            .with_for_update()
        )
    )
    live = [p for p in pods if p.desired_state == DesiredState.RUNNING.value]

    if dep.status == DeploymentStatus.DELETING.value:
        for pod in live:
            pod.desired_state = DesiredState.TERMINATED.value
        if not pods:  # everything has actually stopped
            session.execute(delete(Pod).where(Pod.deployment_id == dep.id))
            session.delete(dep)
        return

    diff = dep.desired_replicas - len(live)
    if diff > 0:
        for _ in range(min(diff, MAX_CREATES_PER_PASS)):
            pod = build_pod(dep)
            session.add(pod)
            session.flush()
            emit(
                session,
                EventType.POD_CREATED,
                "pod",
                pod.id,
                f"pod {pod.name} created for deployment {dep.name}",
                deployment=dep.name,
            )
    elif diff < 0:
        for pod in pick_victims(live, -diff):
            pod.desired_state = DesiredState.TERMINATED.value


def reconcile_deployments(db: Database, settings: Settings) -> None:
    """Level-triggered: compare desired replicas to live pods and create/remove the difference.
    Idempotent; the deployment row lock prevents two controller passes acting concurrently."""
    with db.transaction() as session:
        ids = list(session.scalars(select(Deployment.id)))
    for dep_id in ids:
        with db.transaction() as session:
            dep = session.scalars(
                select(Deployment).where(Deployment.id == dep_id).with_for_update(skip_locked=True)
            ).first()
            if dep is not None:
                reconcile_deployment(session, dep)

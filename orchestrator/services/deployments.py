from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts.schemas import DeploymentCreate
from orchestrator.common.errors import AppError, ConflictError, NotFoundError
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.models import (
    Deployment,
    DeploymentStatus,
    DesiredState,
    Pod,
    PodStatus,
)


def get_deployment(session: Session, name: str, *, lock: bool = False) -> Deployment:
    stmt = select(Deployment).where(Deployment.name == name)
    if lock:
        stmt = stmt.with_for_update()
    dep = session.scalars(stmt).first()
    if dep is None:
        raise NotFoundError(f"deployment {name!r} does not exist", code="DEPLOYMENT_NOT_FOUND")
    return dep


def create_deployment(
    session: Session, body: DeploymentCreate, *, max_replicas: int
) -> tuple[int, Deployment]:
    """201 when created; 200 when an identical template already exists (safe retry).
    A different template under the same name is a 409 (rolling updates are not implemented)."""
    if body.replicas > max_replicas:
        raise AppError(
            f"replicas may not exceed {max_replicas}",
            code="REPLICAS_LIMIT_EXCEEDED",
            status_code=422,
        )
    template = body.template()

    def existing_or_conflict(dep: Deployment) -> tuple[int, Deployment]:
        if dep.template != template:
            raise ConflictError(
                f"deployment {body.name!r} exists with a different spec",
                code="DEPLOYMENT_ALREADY_EXISTS",
            )
        return 200, dep

    found = session.scalars(select(Deployment).where(Deployment.name == body.name)).first()
    if found:
        return existing_or_conflict(found)
    dep = Deployment(name=body.name, template=template, desired_replicas=body.replicas)
    try:
        with session.begin_nested():
            session.add(dep)
            session.flush()
    except IntegrityError:  # a concurrent identical request won the unique-name race
        found = session.scalars(select(Deployment).where(Deployment.name == body.name)).one()
        return existing_or_conflict(found)
    emit(
        session,
        EventType.DEPLOYMENT_CREATED,
        "deployment",
        dep.id,
        f"deployment {dep.name} created with {dep.desired_replicas} replicas",
        name=dep.name,
    )
    return 201, dep


def scale_deployment(
    session: Session, name: str, replicas: int, *, max_replicas: int
) -> Deployment:
    """The row lock serialises concurrent scale calls; they converge on the last writer's value
    and the controller (not the API) creates/removes pods, so nothing is ever duplicated."""
    if replicas > max_replicas:
        raise AppError(
            f"replicas may not exceed {max_replicas}",
            code="REPLICAS_LIMIT_EXCEEDED",
            status_code=422,
        )
    dep = get_deployment(session, name, lock=True)
    if dep.status == DeploymentStatus.DELETING.value:
        raise ConflictError(f"deployment {name!r} is being deleted", code="DEPLOYMENT_DELETING")
    if dep.desired_replicas != replicas:  # idempotent: scaling to the current value is a no-op
        previous, dep.desired_replicas = dep.desired_replicas, replicas
        dep.generation += 1
        emit(
            session,
            EventType.DEPLOYMENT_SCALED,
            "deployment",
            dep.id,
            f"deployment {name} scaled {previous} -> {replicas}",
            previous=previous,
            replicas=replicas,
        )
    return dep


def delete_deployment(session: Session, name: str) -> Deployment:
    dep = get_deployment(session, name, lock=True)
    if dep.status != DeploymentStatus.DELETING.value:
        dep.status = DeploymentStatus.DELETING.value
        dep.desired_replicas = 0
        dep.generation += 1
        emit(
            session,
            EventType.DEPLOYMENT_DELETED,
            "deployment",
            dep.id,
            f"deployment {name} marked for deletion",
            name=name,
        )
    return dep


def replica_counts(session: Session, deployment_ids: list[str]) -> dict[str, dict[str, int]]:
    rows = session.execute(
        select(Pod.deployment_id, Pod.status, Pod.ready, func.count())
        .where(
            Pod.deployment_id.in_(deployment_ids),
            Pod.status != PodStatus.TERMINATED.value,
            Pod.desired_state == DesiredState.RUNNING.value,
        )
        .group_by(Pod.deployment_id, Pod.status, Pod.ready)
    ).all()
    out: dict[str, dict[str, int]] = {d: {"current": 0, "ready": 0} for d in deployment_ids}
    for dep_id, status, ready, count in rows:
        out[dep_id]["current"] += count
        if status == PodStatus.RUNNING.value and ready:
            out[dep_id]["ready"] += count
    return out


def serialize_deployment(dep: Deployment, counts: dict[str, int]) -> dict[str, Any]:
    return {
        "id": dep.id,
        "name": dep.name,
        "status": dep.status,
        "image": dep.template["image"],
        "desired_replicas": dep.desired_replicas,
        "current_replicas": counts["current"],
        "ready_replicas": counts["ready"],
        "generation": dep.generation,
        "template": dep.template,
        "created_at": dep.created_at,
        "updated_at": dep.updated_at,
    }


def new_pod_name(deployment_name: str) -> str:
    return f"{deployment_name}-{uuid.uuid4().hex[:6]}"

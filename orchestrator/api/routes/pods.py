from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select

from orchestrator.api.deps import DbDep, PageDep, SettingsDep, require
from orchestrator.api.schemas import Page, PodOut
from orchestrator.common.errors import AppError, NotFoundError, UnavailableError
from orchestrator.persistence.models import (
    Deployment,
    DesiredState,
    Node,
    Pod,
    PodStatus,
)
from orchestrator.security.auth import Permission as P

router = APIRouter(prefix="/api/v1/pods", tags=["pods"])


def _out(pod: Pod, node_name: str | None) -> PodOut:
    out = PodOut.model_validate(pod)
    out.node = node_name
    return out


def _find(session, ident: str) -> tuple[Pod, Node | None]:  # type: ignore[no-untyped-def]
    row = session.execute(
        select(Pod, Node)
        .outerjoin(Node, Node.id == Pod.node_id)
        .where(or_(Pod.id == ident, Pod.name == ident))
    ).first()
    if row is None:
        raise NotFoundError(f"pod {ident!r} does not exist", code="POD_NOT_FOUND")
    return row[0], row[1]


@router.get("", response_model=Page[PodOut], dependencies=[Depends(require(P.PODS_READ))])
def list_pods(
    db: DbDep,
    page: PageDep,
    status: str | None = None,
    node: str | None = None,
    deployment: str | None = None,
    include_terminated: bool = Query(default=False),
) -> Page[PodOut]:
    with db.transaction() as session:
        stmt = select(Pod, Node.hostname).outerjoin(Node, Node.id == Pod.node_id)
        if status:
            stmt = stmt.where(Pod.status == status.upper())
        elif not include_terminated:
            stmt = stmt.where(Pod.status != PodStatus.TERMINATED.value)
        if node:
            stmt = stmt.where(or_(Node.hostname == node, Node.id == node))
        if deployment:
            stmt = stmt.join(Deployment, Deployment.id == Pod.deployment_id).where(
                Deployment.name == deployment
            )
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.execute(
            stmt.order_by(Pod.created_at, Pod.name).limit(page.limit).offset(page.offset)
        ).all()
        return Page(
            items=[_out(p, name) for p, name in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )


@router.get("/{ident}", response_model=PodOut, dependencies=[Depends(require(P.PODS_READ))])
def get_pod(ident: str, db: DbDep) -> PodOut:
    with db.transaction() as session:
        pod, node = _find(session, ident)
        return _out(pod, node.hostname if node else None)


@router.delete(
    "/{ident}",
    status_code=202,
    response_model=PodOut,
    dependencies=[Depends(require(P.PODS_WRITE))],
)
def delete_pod(ident: str, db: DbDep) -> PodOut:
    """Ask for termination; the owning deployment controller creates a replacement."""
    with db.transaction() as session:
        pod, node = _find(session, ident)
        pod = session.scalars(select(Pod).where(Pod.id == pod.id).with_for_update()).one()
        pod.desired_state = DesiredState.TERMINATED.value
        return _out(pod, node.hostname if node else None)


@router.get(
    "/{ident}/logs", response_class=PlainTextResponse, dependencies=[Depends(require(P.LOGS_READ))]
)
def pod_logs(
    ident: str, db: DbDep, settings: SettingsDep, tail: int = Query(default=100, ge=1, le=10000)
) -> str:
    with db.transaction() as session:
        pod, node = _find(session, ident)
        pod_id = pod.id
        agent_url = node.agent_url if node else None
    if not agent_url:
        raise AppError(
            "pod is not placed on a node with a reachable agent",
            code="LOGS_UNAVAILABLE",
            status_code=409,
        )
    if settings.worker_api_key is None:
        raise UnavailableError("WORKER_API_KEY is not configured; cannot reach agents")
    try:
        resp = httpx.get(
            f"{agent_url}/pods/{pod_id}/logs",
            params={"tail": tail},
            headers={"Authorization": f"Bearer {settings.worker_api_key.get_secret_value()}"},
            timeout=settings.agent_request_timeout,
        )
    except httpx.HTTPError as exc:
        raise AppError(
            "worker agent is unreachable", code="AGENT_UNREACHABLE", status_code=502
        ) from exc
    if resp.status_code == 404:
        raise NotFoundError("no container found for pod on its node", code="CONTAINER_NOT_FOUND")
    if resp.status_code != 200:
        raise AppError(f"agent returned {resp.status_code}", code="AGENT_ERROR", status_code=502)
    return resp.text

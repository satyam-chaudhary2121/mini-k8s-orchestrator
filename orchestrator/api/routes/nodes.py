from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select

from contracts.schemas import HeartbeatRequest, NodeRegister, NodeRegisterResponse, PodStatusReport
from orchestrator.api.deps import DbDep, PageDep, SettingsDep, require
from orchestrator.api.schemas import NodeOut, Page
from orchestrator.common.errors import ConflictError, NotFoundError
from orchestrator.persistence.models import Node, Pod, PodStatus
from orchestrator.scheduler.service import node_views
from orchestrator.scheduler.strategies import NodeView
from orchestrator.security.auth import Permission as P
from orchestrator.services import nodes as svc
from orchestrator.services.reports import apply_status_report, assignment_for

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


def _out(node: Node, views: Mapping[str, NodeView]) -> NodeOut:
    view = views.get(node.id)
    out = NodeOut.model_validate(node)
    if view is not None:
        out.cpu_allocated = view.cpu_allocated
        out.memory_allocated_mb = view.memory_allocated_mb
        out.pod_count = view.pod_count
    return out


@router.post(
    "/register", response_model=NodeRegisterResponse, dependencies=[Depends(require(P.AGENT))]
)
def register(body: NodeRegister, db: DbDep, settings: SettingsDep) -> NodeRegisterResponse:
    with db.transaction() as session:
        node = svc.register_node(session, body)
        return NodeRegisterResponse(
            node_id=node.id,
            heartbeat_interval=settings.heartbeat_interval,
            sync_interval=settings.sync_interval,
        )


@router.get("", response_model=Page[NodeOut], dependencies=[Depends(require(P.NODES_READ))])
def list_nodes(db: DbDep, page: PageDep, status: str | None = None) -> Page[NodeOut]:
    with db.transaction() as session:
        stmt = select(Node)
        if status:
            stmt = stmt.where(Node.status == status.upper())
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        nodes = session.scalars(stmt.order_by(Node.hostname).limit(page.limit).offset(page.offset))
        views = {v.id: v for v in node_views(session)}
        return Page(
            items=[_out(n, views) for n in nodes], total=total, limit=page.limit, offset=page.offset
        )


@router.get("/{ident}", response_model=NodeOut, dependencies=[Depends(require(P.NODES_READ))])
def get_node(ident: str, db: DbDep) -> NodeOut:
    with db.transaction() as session:
        node = svc.get_node(session, ident)
        return _out(node, {v.id: v for v in node_views(session)})


@router.delete("/{ident}", status_code=204, dependencies=[Depends(require(P.NODES_ADMIN))])
def delete_node(ident: str, db: DbDep, force: bool = False) -> Response:
    with db.transaction() as session:
        svc.delete_node(session, ident, force=force)
    return Response(status_code=204)


@router.post(
    "/{ident}/drain", response_model=NodeOut, dependencies=[Depends(require(P.NODES_OPERATE))]
)
def drain(ident: str, db: DbDep) -> NodeOut:
    with db.transaction() as session:
        node = svc.drain_node(session, ident)
        return _out(node, {v.id: v for v in node_views(session)})


@router.post(
    "/{ident}/uncordon", response_model=NodeOut, dependencies=[Depends(require(P.NODES_OPERATE))]
)
def uncordon(ident: str, db: DbDep) -> NodeOut:
    with db.transaction() as session:
        node = svc.uncordon_node(session, ident)
        return _out(node, {v.id: v for v in node_views(session)})


@router.post("/{ident}/heartbeat", dependencies=[Depends(require(P.AGENT))])
def heartbeat(
    ident: str, body: HeartbeatRequest, db: DbDep, settings: SettingsDep
) -> dict[str, object]:
    with db.transaction() as session:
        node = svc.record_heartbeat(session, ident, body.runtime_info)
        return {"status": node.status, "heartbeat_interval": settings.heartbeat_interval}


@router.get("/{ident}/assignments", dependencies=[Depends(require(P.AGENT))])
def assignments(ident: str, db: DbDep) -> list[dict[str, object]]:
    """Everything the worker should be running (or tearing down). Workers poll this."""
    with db.transaction() as session:
        node = svc.get_node(session, ident)
        pods = session.scalars(
            select(Pod)
            .where(Pod.node_id == node.id, Pod.status != PodStatus.TERMINATED.value)
            .order_by(Pod.created_at)
        )
        return [assignment_for(p) for p in pods]


@router.put("/{ident}/pods/{pod_id}/status", dependencies=[Depends(require(P.AGENT))])
def report_status(ident: str, pod_id: str, body: PodStatusReport, db: DbDep) -> dict[str, str]:
    with db.transaction() as session:
        node = svc.get_node(session, ident)
        pod = session.scalars(select(Pod).where(Pod.id == pod_id).with_for_update()).first()
        if pod is None:
            raise NotFoundError(f"pod {pod_id!r} does not exist", code="POD_NOT_FOUND")
        if pod.node_id != node.id:
            raise ConflictError("pod is not assigned to this node", code="POD_NOT_ON_NODE")
        apply_status_report(session, pod, body)
        return {"status": pod.status}


_ = Annotated, Query  # imported for FastAPI signature introspection in future routes

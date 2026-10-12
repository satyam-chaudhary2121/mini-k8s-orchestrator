from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from contracts.schemas import ServiceCreate
from orchestrator.api.deps import DbDep, PageDep, require
from orchestrator.api.schemas import EndpointOut, Page, ServiceDetail, ServiceOut
from orchestrator.common.errors import AppError, ConflictError, NotFoundError
from orchestrator.discovery.balancer import (
    DatabaseServiceDiscovery,
    Endpoint,
    LoadBalancer,
    ServiceDiscovery,
)
from orchestrator.events.recorder import EventType, emit
from orchestrator.persistence.models import Service
from orchestrator.security.auth import Permission as P

router = APIRouter(prefix="/api/v1/services", tags=["services"])
_discovery: ServiceDiscovery = DatabaseServiceDiscovery()


def _get(session, name: str) -> Service:  # type: ignore[no-untyped-def]
    svc = session.scalars(select(Service).where(Service.name == name)).first()
    if svc is None:
        raise NotFoundError(f"service {name!r} does not exist", code="SERVICE_NOT_FOUND")
    return svc


def _detail(session, svc: Service) -> ServiceDetail:  # type: ignore[no-untyped-def]
    eps = _discovery.endpoints(session, svc)
    base = ServiceOut.model_validate(svc).model_dump()
    return ServiceDetail(
        **base,
        endpoints=[
            EndpointOut(pod=e.pod_name, pod_id=e.pod_id, address=e.address, port=e.port)
            for e in eps
        ],
    )


@router.post(
    "",
    response_model=ServiceDetail,
    status_code=201,
    dependencies=[Depends(require(P.SERVICES_WRITE))],
)
def create(body: ServiceCreate, db: DbDep, response: Response) -> ServiceDetail:
    with db.transaction() as session:
        existing = session.scalars(select(Service).where(Service.name == body.name)).first()
        if existing is None:
            try:
                with session.begin_nested():
                    existing = Service(**body.model_dump())
                    session.add(existing)
                    session.flush()
                emit(
                    session,
                    EventType.SERVICE_CREATED,
                    "service",
                    existing.id,
                    f"service {body.name} created",
                    name=body.name,
                )
                return _detail(session, existing)
            except IntegrityError:
                existing = session.scalars(select(Service).where(Service.name == body.name)).one()
        same = (existing.selector, existing.port, existing.target_port) == (
            body.selector,
            body.port,
            body.target_port,
        )
        if not same:
            raise ConflictError(
                f"service {body.name!r} exists with a different spec", code="SERVICE_ALREADY_EXISTS"
            )
        response.status_code = 200
        return _detail(session, existing)


@router.get("", response_model=Page[ServiceOut], dependencies=[Depends(require(P.SERVICES_READ))])
def list_services(db: DbDep, page: PageDep) -> Page[ServiceOut]:
    with db.transaction() as session:
        total = session.scalar(select(func.count()).select_from(Service)) or 0
        rows = session.scalars(
            select(Service).order_by(Service.name).limit(page.limit).offset(page.offset)
        )
        return Page(
            items=[ServiceOut.model_validate(s) for s in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )


@router.get(
    "/{name}", response_model=ServiceDetail, dependencies=[Depends(require(P.SERVICES_READ))]
)
def get_service(name: str, db: DbDep) -> ServiceDetail:
    with db.transaction() as session:
        return _detail(session, _get(session, name))


@router.delete("/{name}", status_code=204, dependencies=[Depends(require(P.SERVICES_WRITE))])
def delete_service(name: str, db: DbDep) -> Response:
    with db.transaction() as session:
        svc = _get(session, name)
        emit(session, EventType.SERVICE_DELETED, "service", svc.id, f"service {name} deleted")
        session.delete(svc)
    return Response(status_code=204)


@router.get(
    "/{name}/resolve", response_model=EndpointOut, dependencies=[Depends(require(P.SERVICES_READ))]
)
def resolve(name: str, request: Request, db: DbDep) -> EndpointOut:
    """Round-robin over ready endpoints. 503 when none are healthy."""
    balancer: LoadBalancer = request.app.state.balancer
    with db.transaction() as session:
        svc = _get(session, name)
        endpoints: list[Endpoint] = _discovery.endpoints(session, svc)
    chosen = balancer.pick(name, endpoints)
    if chosen is None:
        raise AppError(
            f"service {name!r} has no ready endpoints", code="NO_READY_ENDPOINTS", status_code=503
        )
    return EndpointOut(
        pod=chosen.pod_name, pod_id=chosen.pod_id, address=chosen.address, port=chosen.port
    )

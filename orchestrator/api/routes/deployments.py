from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from contracts.schemas import DeploymentCreate, ScaleRequest
from orchestrator.api.deps import DbDep, PageDep, SettingsDep, require
from orchestrator.api.schemas import Page
from orchestrator.persistence.models import Deployment
from orchestrator.security.auth import Permission as P
from orchestrator.security.auth import Principal
from orchestrator.services import deployments as svc
from orchestrator.services.idempotency import hash_request, run_idempotent

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])
IdemKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)]


def _json(status: int, body: dict[str, Any], replayed: bool) -> JSONResponse:
    headers = {"Idempotent-Replayed": "true"} if replayed else None
    return JSONResponse(body, status_code=status, headers=headers)


def _one(session: Any, dep: Deployment) -> dict[str, Any]:
    counts = svc.replica_counts(session, [dep.id])[dep.id]
    return svc.serialize_deployment(dep, counts)


@router.post("", status_code=201)
def create(
    body: DeploymentCreate,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: IdemKey = None,
    principal: Principal = Depends(require(P.DEPLOYMENTS_WRITE)),
) -> JSONResponse:
    with db.transaction() as session:

        def handler() -> tuple[int, dict[str, Any]]:
            status, dep = svc.create_deployment(session, body, max_replicas=settings.max_replicas)
            return status, _jsonable(_one(session, dep))

        status, payload, replayed = run_idempotent(
            session,
            key=idempotency_key,
            scope=f"{principal.name}:POST:/deployments",
            request_hash=hash_request(body.model_dump()),
            handler=handler,
        )
    return _json(status, payload, replayed)


@router.get("", dependencies=[Depends(require(P.DEPLOYMENTS_READ))])
def list_deployments(db: DbDep, page: PageDep) -> Page[dict[str, Any]]:
    with db.transaction() as session:
        total = session.scalar(select(func.count()).select_from(Deployment)) or 0
        deps = list(
            session.scalars(
                select(Deployment).order_by(Deployment.name).limit(page.limit).offset(page.offset)
            )
        )
        counts = svc.replica_counts(session, [d.id for d in deps])
        return Page(
            items=[svc.serialize_deployment(d, counts[d.id]) for d in deps],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )


@router.get("/{name}", dependencies=[Depends(require(P.DEPLOYMENTS_READ))])
def get_deployment(name: str, db: DbDep) -> dict[str, Any]:
    with db.transaction() as session:
        return _one(session, svc.get_deployment(session, name))


@router.delete("/{name}", status_code=202, dependencies=[Depends(require(P.DEPLOYMENTS_WRITE))])
def delete(name: str, db: DbDep) -> dict[str, Any]:
    with db.transaction() as session:
        return _one(session, svc.delete_deployment(session, name))


@router.post("/{name}/scale")
def scale(
    name: str,
    body: ScaleRequest,
    db: DbDep,
    settings: SettingsDep,
    idempotency_key: IdemKey = None,
    principal: Principal = Depends(require(P.DEPLOYMENTS_WRITE)),
) -> JSONResponse:
    with db.transaction() as session:

        def handler() -> tuple[int, dict[str, Any]]:
            dep = svc.scale_deployment(
                session, name, body.replicas, max_replicas=settings.max_replicas
            )
            return 200, _jsonable(_one(session, dep))

        status, payload, replayed = run_idempotent(
            session,
            key=idempotency_key,
            scope=f"{principal.name}:POST:/deployments/{name}/scale",
            request_hash=hash_request(body.model_dump()),
            handler=handler,
        )
    return _json(status, payload, replayed)


def _jsonable(data: dict[str, Any]) -> dict[str, Any]:
    """Make the payload storable in a JSONB idempotency record (datetimes -> ISO strings)."""
    from fastapi.encoders import jsonable_encoder

    result: dict[str, Any] = jsonable_encoder(data)
    return result


_ = Response

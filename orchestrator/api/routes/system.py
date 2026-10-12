from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select

from orchestrator import metrics
from orchestrator.api.deps import DbDep, PageDep, require
from orchestrator.api.schemas import EventOut, Page
from orchestrator.persistence.models import Event
from orchestrator.security.auth import Permission as P

router = APIRouter()


@router.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/ready", tags=["system"])
def ready(db: DbDep, response: Response) -> dict[str, str]:
    """Readiness: the database is reachable."""
    try:
        db.ping()
    except Exception:
        response.status_code = 503
        return {"status": "unavailable"}
    return {"status": "ready"}


@router.get("/metrics", tags=["system"])
def prometheus_metrics(db: DbDep) -> Response:
    with db.transaction() as session:
        metrics.refresh_gauges(session)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get(
    "/api/v1/events",
    response_model=Page[EventOut],
    tags=["events"],
    dependencies=[Depends(require(P.EVENTS_READ))],
)
def list_events(
    db: DbDep,
    page: PageDep,
    type: str | None = None,
    object_kind: str | None = None,
    object_id: str | None = None,
) -> Page[EventOut]:
    with db.transaction() as session:
        stmt = select(Event)
        if type:
            stmt = stmt.where(Event.type == type.upper())
        if object_kind:
            stmt = stmt.where(Event.object_kind == object_kind)
        if object_id:
            stmt = stmt.where(Event.object_id == object_id)
        total = len(session.scalars(stmt.with_only_columns(Event.id)).all())
        rows = session.scalars(stmt.order_by(Event.id.desc()).limit(page.limit).offset(page.offset))
        return Page(
            items=[EventOut.model_validate(e) for e in rows],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )

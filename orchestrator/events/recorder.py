from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from orchestrator.persistence.models import Event

log = logging.getLogger("orchestrator.events")


class EventType(StrEnum):
    NODE_REGISTERED = "NODE_REGISTERED"
    NODE_HEARTBEAT = "NODE_HEARTBEAT"
    NODE_READY = "NODE_READY"
    NODE_UNHEALTHY = "NODE_UNHEALTHY"
    NODE_OFFLINE = "NODE_OFFLINE"
    NODE_DRAINING = "NODE_DRAINING"
    NODE_DRAINED = "NODE_DRAINED"
    NODE_DELETED = "NODE_DELETED"
    DEPLOYMENT_CREATED = "DEPLOYMENT_CREATED"
    DEPLOYMENT_SCALED = "DEPLOYMENT_SCALED"
    DEPLOYMENT_DELETED = "DEPLOYMENT_DELETED"
    POD_CREATED = "POD_CREATED"
    POD_SCHEDULED = "POD_SCHEDULED"
    POD_UNSCHEDULABLE = "POD_UNSCHEDULABLE"
    POD_STARTED = "POD_STARTED"
    POD_UNHEALTHY = "POD_UNHEALTHY"
    POD_FAILED = "POD_FAILED"
    POD_RESTARTED = "POD_RESTARTED"
    POD_DELETED = "POD_DELETED"
    SERVICE_CREATED = "SERVICE_CREATED"
    SERVICE_DELETED = "SERVICE_DELETED"


def emit(
    session: Session,
    type_: EventType,
    object_kind: str,
    object_id: str,
    message: str,
    **payload: Any,
) -> None:
    """Persist an event in the caller's transaction (so it commits atomically with the change)."""
    session.add(
        Event(
            type=type_.value,
            object_kind=object_kind,
            object_id=object_id,
            message=message,
            payload=payload,
        )
    )
    log.info(
        message,
        extra={
            "event_type": type_.value,
            "object_kind": object_kind,
            "object_id": object_id,
            "event_payload": payload,  # namespaced: payload keys may clash with LogRecord fields
        },
    )

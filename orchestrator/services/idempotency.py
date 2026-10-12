"""Idempotency-Key support.

The key row is inserted in the *same transaction* as the work it protects. A concurrent duplicate
blocks on the unique index until the first request commits, then replays its stored response; if
the first request fails, its transaction (and therefore the key) rolls back so a retry re-executes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from orchestrator.common.errors import AppError, ConflictError
from orchestrator.persistence.models import IdempotencyKey

Handler = Callable[[], tuple[int, dict[str, Any]]]


def hash_request(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _replay(row: IdempotencyKey, request_hash: str) -> tuple[int, dict[str, Any], bool]:
    if row.request_hash != request_hash:
        raise AppError(
            "Idempotency-Key was already used with a different request",
            code="IDEMPOTENCY_KEY_REUSED",
            status_code=422,
        )
    if row.status_code is None or row.response is None:
        raise ConflictError(
            "a request with this Idempotency-Key is still in progress",
            code="IDEMPOTENCY_IN_PROGRESS",
        )
    return row.status_code, row.response, True


def run_idempotent(
    session: Session, *, key: str | None, scope: str, request_hash: str, handler: Handler
) -> tuple[int, dict[str, Any], bool]:
    """Returns (status_code, body, replayed)."""
    if not key:
        status, body = handler()
        return status, body, False

    def find() -> IdempotencyKey | None:
        return session.scalars(
            select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        ).first()

    if (existing := find()) is not None:
        return _replay(existing, request_hash)
    row = IdempotencyKey(scope=scope, key=key, request_hash=request_hash)
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = find()
        if existing is None:  # pragma: no cover - defensive
            raise
        return _replay(existing, request_hash)
    status, body = handler()
    row.status_code, row.response = status, body
    session.flush()
    return status, body, False

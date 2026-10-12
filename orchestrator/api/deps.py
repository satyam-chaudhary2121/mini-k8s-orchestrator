from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Query, Request

from orchestrator.common.errors import ForbiddenError, UnauthorizedError
from orchestrator.config.settings import Settings
from orchestrator.persistence.db import Database
from orchestrator.security.auth import AuthenticationProvider, Permission, Principal


def get_db(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


DbDep = Annotated[Database, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def _extract_token(authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return x_api_key


def require(permission: Permission) -> Callable[..., Principal]:
    def dependency(
        request: Request,
        db: DbDep,
        authorization: Annotated[str | None, Header()] = None,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> Principal:
        token = _extract_token(authorization, x_api_key)
        if not token:
            raise UnauthorizedError("missing credentials", code="MISSING_CREDENTIALS")
        provider: AuthenticationProvider = request.app.state.auth
        with db.transaction() as session:
            principal = provider.authenticate(session, token)
        if principal is None:
            raise UnauthorizedError("invalid credentials", code="INVALID_CREDENTIALS")
        if not principal.can(permission):
            raise ForbiddenError(
                f"role {principal.role.value} lacks permission {permission.value}",
                code="PERMISSION_DENIED",
            )
        request.state.principal = principal
        return principal

    return dependency


class Pagination:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> None:
        self.limit, self.offset = limit, offset


PageDep = Annotated[Pagination, Depends()]

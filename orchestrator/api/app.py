from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from orchestrator.api.errors import install_error_handlers
from orchestrator.api.middleware import install_middleware
from orchestrator.api.routes import deployments, nodes, pods, services, system
from orchestrator.common.logging import configure_logging
from orchestrator.common.retry import retry
from orchestrator.config.settings import Settings, get_settings
from orchestrator.controller.loops import build_loops
from orchestrator.controller.supervisor import Supervisor
from orchestrator.discovery.balancer import RoundRobinBalancer
from orchestrator.persistence.db import Database
from orchestrator.security.auth import (
    ApiKeyAuthProvider,
    AuthenticationProvider,
    bootstrap_api_keys,
)

log = logging.getLogger("orchestrator")


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    auth: AuthenticationProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    database = db or Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        def bootstrap() -> None:
            with database.transaction() as session:
                bootstrap_api_keys(session, settings)

        retry(
            bootstrap,
            retry_on=(OperationalError,),
            attempts=8,
            base=1.0,
            maximum=10.0,
            operation="database bootstrap",
        )
        supervisor: Supervisor | None = None
        if settings.run_controllers:
            supervisor = Supervisor(
                build_loops(database, settings), max_backoff=settings.controller_max_backoff
            )
            supervisor.start()
        log.info("control plane started")
        try:
            yield
        finally:  # graceful shutdown: stop loops first, then release the DB pool
            log.info("control plane stopping")
            if supervisor:
                await supervisor.stop()
            database.dispose()

    app = FastAPI(
        title="Mini Kubernetes-Inspired Container Orchestrator", version="0.1.0", lifespan=lifespan
    )
    app.state.settings = settings
    app.state.db = database
    app.state.auth = auth or ApiKeyAuthProvider()
    app.state.balancer = RoundRobinBalancer()
    install_middleware(app)
    install_error_handlers(app)
    for module in (system, nodes, deployments, pods, services):
        app.include_router(module.router)
    return app

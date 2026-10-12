from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response

from orchestrator import metrics

log = logging.getLogger("orchestrator.access")


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        status = 500
        try:
            response: Response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            metrics.REQUESTS.labels(request.method, path, str(status)).inc()
            metrics.REQUEST_DURATION.labels(request.method, path).observe(elapsed)
            log.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "latency_ms": round(elapsed * 1000, 2),
                },
            )

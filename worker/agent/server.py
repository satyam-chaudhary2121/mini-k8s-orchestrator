"""Tiny HTTP server so the control plane can fetch container logs from this node."""

from __future__ import annotations

import hmac

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse

from worker.runtime.base import LABEL_EPOCH, LABEL_POD_ID, ContainerRuntime, RuntimeFailure


def create_agent_app(runtime: ContainerRuntime, api_key: str) -> FastAPI:
    app = FastAPI(title="worker agent", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/pods/{pod_id}/logs", response_class=PlainTextResponse)
    def logs(
        pod_id: str,
        tail: int = Query(default=100, ge=1, le=10000),
        authorization: str | None = Header(default=None),
    ) -> str:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, api_key):
            raise HTTPException(status_code=401, detail="unauthorized")
        try:
            containers = runtime.list_containers({LABEL_POD_ID: pod_id})
            if not containers:
                raise HTTPException(status_code=404, detail="no container for pod")
            latest = max(containers, key=lambda c: int(c.labels.get(LABEL_EPOCH, "0")))
            return runtime.get_logs(latest.id, tail=tail)
        except RuntimeFailure as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app

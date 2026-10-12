from __future__ import annotations

from typing import Any

import httpx

from contracts.schemas import NodeRegister, PodStatusReport


class ControlPlaneError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code = status, code


class ControlPlaneUnreachable(Exception):
    pass


class NodeUnknown(ControlPlaneError):
    """The control plane does not know this node id (e.g. its database was reset)."""


class ControlPlaneClient:
    """Thin synchronous client for the endpoints a worker uses."""

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise ControlPlaneUnreachable(str(exc)) from exc
        if response.status_code >= 400:
            try:
                err = response.json()["error"]
                code, message = err["code"], err["message"]
            except (ValueError, KeyError, TypeError):
                code, message = "HTTP_ERROR", response.text[:200]
            cls = NodeUnknown if code == "NODE_NOT_FOUND" else ControlPlaneError
            raise cls(response.status_code, code, message)
        return response.json() if response.content else None

    def register(self, body: NodeRegister) -> dict[str, Any]:
        return self._request("POST", "/api/v1/nodes/register", json=body.model_dump())

    def heartbeat(
        self, node_id: str, runtime_info: dict[str, Any] | None, running: int | None
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/nodes/{node_id}/heartbeat",
            json={"runtime_info": runtime_info, "running_containers": running},
        )

    def assignments(self, node_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/nodes/{node_id}/assignments")

    def report(self, node_id: str, pod_id: str, report: PodStatusReport) -> dict[str, Any]:
        return self._request(
            "PUT", f"/api/v1/nodes/{node_id}/pods/{pod_id}/status", json=report.model_dump()
        )

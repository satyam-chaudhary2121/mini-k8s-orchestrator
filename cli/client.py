from __future__ import annotations

import time
from typing import Any

import httpx


class CliError(Exception):
    pass


class ApiClient:
    """HTTP client for the REST API; the CLI never touches the database."""

    def __init__(self, url: str, api_key: str | None, *, retries: int = 3) -> None:
        if not api_key:
            raise CliError("no API key: set ORCHESTRATOR_API_KEY or pass --api-key")
        self._http = httpx.Client(
            base_url=url, timeout=15.0, headers={"Authorization": f"Bearer {api_key}"}
        )
        self._retries = retries

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                resp = self._http.request(method, path, **kwargs)
                break
            except httpx.TransportError as exc:  # same Idempotency-Key is reused on retry
                last = exc
                time.sleep(2**attempt * 0.5)
        else:
            raise CliError(f"cannot reach control plane: {last}")
        if resp.status_code >= 400:
            try:
                err = resp.json()["error"]
                raise CliError(f"{err['code']}: {err['message']}")
            except (ValueError, KeyError):
                raise CliError(f"HTTP {resp.status_code}: {resp.text[:200]}") from None
        if not resp.content:
            return None
        return resp.text if "text/plain" in resp.headers.get("content-type", "") else resp.json()

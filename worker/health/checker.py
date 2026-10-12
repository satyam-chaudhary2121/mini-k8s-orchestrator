from __future__ import annotations

from abc import ABC, abstractmethod

import httpx


class HealthChecker(ABC):
    @abstractmethod
    def check_http(self, host: str, port: int, path: str, timeout: float) -> bool: ...


class HttpHealthChecker(HealthChecker):
    """Healthy means the endpoint answered with a status below 400 within the timeout."""

    def check_http(self, host: str, port: int, path: str, timeout: float) -> bool:
        try:
            response = httpx.get(
                f"http://{host}:{port}{path}", timeout=timeout, follow_redirects=False
            )
        except httpx.HTTPError:
            return False
        return response.status_code < 400

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import docker
import requests
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from orchestrator.common.retry import retry
from worker.runtime.base import (
    ContainerCreateError,
    ContainerInfo,
    ContainerNotFoundError,
    ContainerRuntime,
    ContainerSpec,
    ImageNotFoundError,
    RuntimeUnavailableError,
)

log = logging.getLogger("worker.docker")
_TRANSIENT = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


def _translate(exc: Exception) -> Exception:
    """Map Docker SDK errors onto the runtime's transient/permanent taxonomy."""
    if isinstance(exc, ImageNotFound):
        return ImageNotFoundError(str(exc))
    if isinstance(exc, NotFound):
        return ContainerNotFoundError(str(exc))
    if isinstance(exc, APIError):
        status = exc.status_code or 0
        text = str(exc.explanation or exc).lower()
        pull_failed = any(
            m in text
            for m in ("pull access denied", "manifest unknown", "repository does not exist")
        )
        if pull_failed and status in (404, 500):
            return ImageNotFoundError(str(exc.explanation or exc))
        if status >= 500:
            return RuntimeUnavailableError(str(exc))
        return ContainerCreateError(str(exc.explanation or exc))
    if isinstance(exc, (*_TRANSIENT, DockerException)):
        return RuntimeUnavailableError(str(exc))
    return exc


class DockerRuntime(ContainerRuntime):
    """Docker Engine implementation. Transient failures are retried with exponential backoff
    (1s, 2s, 4s); permanent ones (missing image, rejected config) surface immediately."""

    def __init__(
        self,
        client: docker.DockerClient | None = None,
        *,
        timeout: int = 60,
        attempts: int = 4,
        retry_base: float = 1.0,
    ) -> None:
        self._client = client or docker.from_env(timeout=timeout)
        self._attempts = attempts
        self._retry_base = retry_base

    @property
    def host(self) -> str:
        docker_host = os.environ.get("DOCKER_HOST", "")
        parsed = urlparse(docker_host)
        return (
            parsed.hostname
            if parsed.scheme in ("tcp", "http", "https") and parsed.hostname
            else "127.0.0.1"
        )

    def _call(self, func: Any, *args: Any, operation: str, **kwargs: Any) -> Any:
        def attempt() -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                raise _translate(exc) from exc

        return retry(
            attempt,
            retry_on=(RuntimeUnavailableError,),
            attempts=self._attempts,
            base=self._retry_base,
            operation=operation,
        )

    def info(self) -> dict[str, Any]:
        raw = self._call(self._client.info, operation="docker info")
        return {
            "server_version": raw.get("ServerVersion"),
            "os": raw.get("OperatingSystem"),
            "containers": raw.get("Containers"),
            "ncpu": raw.get("NCPU"),
            "mem_total_bytes": raw.get("MemTotal"),
        }

    def pull_image(self, image: str) -> None:
        """Pull; if the registry is unreachable but the image is cached locally, use the cache."""
        try:
            self._call(self._client.images.pull, image, operation=f"pull {image}")
        except RuntimeUnavailableError:
            try:
                self._client.images.get(image)
                log.warning("registry unreachable, using local image", extra={"image": image})
            except DockerException:
                raise

    def create_container(self, spec: ContainerSpec) -> str:
        command = None
        if spec.command is not None:
            command = [*spec.command, *spec.args]
        elif spec.args:
            command = list(spec.args)
        kwargs: dict[str, Any] = {
            "name": spec.name,
            "command": command,
            "environment": spec.env,
            "labels": spec.labels,
            "ports": {f"{p}/tcp": None for p in spec.ports},
            "detach": True,
        }
        if spec.command is not None:
            kwargs["entrypoint"] = None
        if spec.cpu:
            kwargs["nano_cpus"] = int(spec.cpu * 1_000_000_000)
        if spec.memory_mb:
            kwargs["mem_limit"] = f"{spec.memory_mb}m"
        container = self._call(
            self._client.containers.create, spec.image, operation=f"create {spec.name}", **kwargs
        )
        return str(container.id)

    def _get(self, container_id: str) -> Any:
        return self._call(self._client.containers.get, container_id, operation="get container")

    def start_container(self, container_id: str) -> None:
        self._call(self._get(container_id).start, operation=f"start {container_id[:12]}")

    def stop_container(self, container_id: str, timeout: int = 10) -> None:
        try:
            self._call(
                self._get(container_id).stop, timeout=timeout, operation=f"stop {container_id[:12]}"
            )
        except ContainerNotFoundError:
            return  # idempotent

    def restart_container(self, container_id: str, timeout: int = 10) -> None:
        self._call(
            self._get(container_id).restart,
            timeout=timeout,
            operation=f"restart {container_id[:12]}",
        )

    def remove_container(self, container_id: str, force: bool = True) -> None:
        try:
            self._call(
                self._get(container_id).remove, force=force, operation=f"remove {container_id[:12]}"
            )
        except ContainerNotFoundError:
            return  # idempotent

    @staticmethod
    def _to_info(container: Any) -> ContainerInfo:
        attrs = container.attrs
        state = attrs.get("State", {})
        ports: dict[int, int] = {}
        for key, bindings in (attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
            if bindings:
                ports[int(key.split("/")[0])] = int(bindings[0]["HostPort"])
        started = state.get("StartedAt")
        started_at = None
        if started and not started.startswith("0001"):
            started_at = datetime.fromisoformat(started[:26].rstrip("Z") + "+00:00")
        exit_code = state.get("ExitCode") if state.get("Status") in ("exited", "dead") else None
        return ContainerInfo(
            id=container.id,
            name=container.name,
            status=state.get("Status", "unknown"),
            exit_code=exit_code,
            labels=container.labels or {},
            ports=ports,
            started_at=started_at,
        )

    def inspect_container(self, container_id: str) -> ContainerInfo:
        container = self._get(container_id)
        self._call(container.reload, operation="inspect")
        return self._to_info(container)

    def get_logs(self, container_id: str, tail: int = 100) -> str:
        raw: bytes = self._call(
            self._get(container_id).logs, tail=tail, timestamps=True, operation="logs"
        )
        return raw.decode("utf-8", errors="replace")

    def list_containers(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        filters = {"label": [f"{k}={v}" for k, v in (labels or {}).items()]}
        containers = self._call(
            self._client.containers.list, all=True, filters=filters, operation="list containers"
        )
        return [self._to_info(c) for c in containers]

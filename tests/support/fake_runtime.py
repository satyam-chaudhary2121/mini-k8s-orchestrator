"""In-memory ContainerRuntime for tests of control-plane and agent logic without Docker.

The real DockerRuntime is covered by tests/integration/test_docker_runtime.py (marker ``docker``).
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

from worker.health.checker import HealthChecker
from worker.runtime.base import (
    ContainerInfo,
    ContainerNotFoundError,
    ContainerRuntime,
    ContainerSpec,
    ImageNotFoundError,
)


class FakeRuntime(ContainerRuntime):
    def __init__(self, host: str = "127.0.0.1") -> None:
        self._host = host
        self.containers: dict[str, dict[str, Any]] = {}
        self.pulled: list[str] = []
        self._ports = itertools.count(30000)

    @property
    def host(self) -> str:
        return self._host

    def info(self) -> dict[str, Any]:
        return {"server_version": "fake"}

    def pull_image(self, image: str) -> None:
        if image.startswith("invalid/") or image.endswith(":does-not-exist"):
            raise ImageNotFoundError(f"pull access denied for {image}")
        self.pulled.append(image)

    def create_container(self, spec: ContainerSpec) -> str:
        cid = uuid.uuid4().hex
        self.containers[cid] = {
            "spec": spec,
            "status": "created",
            "exit_code": None,
            "ports": {p: next(self._ports) for p in spec.ports},
        }
        return cid

    def _get(self, cid: str) -> dict[str, Any]:
        try:
            return self.containers[cid]
        except KeyError:
            raise ContainerNotFoundError(cid) from None

    def start_container(self, container_id: str) -> None:
        self._get(container_id)["status"] = "running"

    def stop_container(self, container_id: str, timeout: int = 10) -> None:
        if container_id in self.containers and self.containers[container_id]["status"] == "running":
            self.containers[container_id].update(status="exited", exit_code=0)

    def restart_container(self, container_id: str, timeout: int = 10) -> None:
        self._get(container_id).update(status="running", exit_code=None)

    def remove_container(self, container_id: str, force: bool = True) -> None:
        self.containers.pop(container_id, None)

    def inspect_container(self, container_id: str) -> ContainerInfo:
        c = self._get(container_id)
        return ContainerInfo(
            id=container_id,
            name=c["spec"].name,
            status=c["status"],
            exit_code=c["exit_code"],
            labels=c["spec"].labels,
            ports=c["ports"],
        )

    def get_logs(self, container_id: str, tail: int = 100) -> str:
        return f"log output of {self._get(container_id)['spec'].name}\n"

    def list_containers(self, labels: dict[str, str] | None = None) -> list[ContainerInfo]:
        wanted = labels or {}
        return [
            self.inspect_container(cid)
            for cid, c in self.containers.items()
            if all(c["spec"].labels.get(k) == v for k, v in wanted.items())
        ]

    # test helpers -------------------------------------------------------------------------
    def kill_all(self, exit_code: int = 137) -> None:
        for c in self.containers.values():
            if c["status"] == "running":
                c.update(status="exited", exit_code=exit_code)

    def kill(self, container_id: str, exit_code: int = 137) -> None:
        self._get(container_id).update(status="exited", exit_code=exit_code)

    def running(self) -> list[str]:
        return [cid for cid, c in self.containers.items() if c["status"] == "running"]


class FakeHealthChecker(HealthChecker):
    def __init__(self) -> None:
        self.down: set[int] = set()
        self.all_down = False

    def check_http(self, host: str, port: int, path: str, timeout: float) -> bool:
        return not self.all_down and port not in self.down

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from contracts.schemas import NodeRegister, PodStatusReport
from orchestrator.common.retry import backoff_delays
from worker.agent.client import (
    ControlPlaneClient,
    ControlPlaneError,
    ControlPlaneUnreachable,
    NodeUnknown,
)
from worker.config import WorkerSettings
from worker.health.checker import HealthChecker
from worker.reporting.oplog import operation
from worker.runtime.base import (
    LABEL_EPOCH,
    LABEL_MANAGED,
    LABEL_POD_ID,
    LABEL_POD_NAME,
    ContainerCreateError,
    ContainerInfo,
    ContainerRuntime,
    ContainerSpec,
    ImageNotFoundError,
    RuntimeFailure,
    RuntimeUnavailableError,
)

log = logging.getLogger("worker.agent")
ACTIVE = {"SCHEDULED", "CREATING", "RUNNING", "UNHEALTHY"}


@dataclass
class HealthState:
    last_checked: float = 0.0
    healthy: bool = False
    failures: int = 0


class WorkerAgent:
    """Owns Docker on one node: registers, heartbeats, and converges local containers to the
    assignments the control plane publishes (a kubelet-style pull model)."""

    def __init__(
        self,
        settings: WorkerSettings,
        client: ControlPlaneClient,
        runtime: ContainerRuntime,
        health_checker: HealthChecker,
    ) -> None:
        self.settings = settings
        self.client = client
        self.runtime = runtime
        self.health_checker = health_checker
        self.node_id: str | None = None
        self.heartbeat_interval = 10.0
        self.sync_interval = 3.0
        self._health: dict[str, HealthState] = {}
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ control plane
    def register(self) -> None:
        body = NodeRegister(
            hostname=self.settings.node_hostname,
            ip=self.settings.advertise_ip,
            cpu=self.settings.cpu,
            memory=self.settings.memory_mb,
            labels=self.settings.node_labels,
            agent_url=self.settings.public_agent_url,
            runtime_info=self.runtime.info(),
        )
        resp = self.client.register(body)
        self.node_id = resp["node_id"]
        self.heartbeat_interval = float(resp["heartbeat_interval"])
        self.sync_interval = float(resp["sync_interval"])
        log.info("registered", extra={"node_id": self.node_id, "hostname": body.hostname})

    def heartbeat_once(self) -> None:
        assert self.node_id
        try:
            running = len(self.runtime.list_containers({LABEL_MANAGED: "true"}))
            self.client.heartbeat(self.node_id, None, running)
        except NodeUnknown:
            log.warning("control plane forgot this node; re-registering")
            self.register()

    # ------------------------------------------------------------------ reconciliation
    def sync_once(self) -> None:
        """Compare assignments (desired) with local containers (actual) and converge."""
        assert self.node_id
        assignments = self.client.assignments(self.node_id)
        containers: dict[str, list[ContainerInfo]] = {}
        for info in self.runtime.list_containers({LABEL_MANAGED: "true"}):
            containers.setdefault(info.labels.get(LABEL_POD_ID, ""), []).append(info)

        assigned_ids = {a["id"] for a in assignments}
        for assignment in assignments:
            try:
                self._reconcile_pod(assignment, containers.get(assignment["id"], []))
            except (RuntimeUnavailableError, ControlPlaneUnreachable) as exc:
                log.warning(
                    "transient error; will retry next sync",
                    extra={"pod_id": assignment["id"], "error": str(exc)},
                )
            except Exception:
                log.exception("pod reconcile failed", extra={"pod_id": assignment["id"]})
        for pod_id, infos in containers.items():  # orphans: not assigned to this node any more
            if pod_id not in assigned_ids:
                for info in infos:
                    self._remove(pod_id, info)
                self._health.pop(pod_id, None)

    def _reconcile_pod(self, a: dict[str, Any], infos: list[ContainerInfo]) -> None:
        pod_id, status = a["id"], a["status"]
        if a["desired_state"] == "TERMINATED":
            for info in infos:
                self._remove(pod_id, info)
            self._health.pop(pod_id, None)
            if status != "TERMINATED":
                self._report(pod_id, PodStatusReport(status="TERMINATED"))
            return
        if status == "FAILED":
            return  # the control plane decides whether/when to restart (bumps restart_epoch)

        epoch = str(a["restart_epoch"])
        current = [i for i in infos if i.labels.get(LABEL_EPOCH) == epoch]
        for stale in (i for i in infos if i.labels.get(LABEL_EPOCH) != epoch):
            self._remove(pod_id, stale)
        if not current:
            self._start_pod(a)
            return
        info = current[0]
        if info.status == "created":
            self.runtime.start_container(info.id)
            info = self.runtime.inspect_container(info.id)
        if info.status == "running":
            self._observe_running(a, info)
        elif info.status in ("exited", "dead"):
            self._report(
                pod_id,
                PodStatusReport(
                    status="FAILED",
                    reason="ContainerExited",
                    exit_code=info.exit_code,
                    container_id=info.id,
                    message=f"container exited with code {info.exit_code}",
                ),
            )

    def _start_pod(self, a: dict[str, Any]) -> None:
        pod_id, spec = a["id"], a["spec"]
        if a["status"] == "SCHEDULED":
            self._report(pod_id, PodStatusReport(status="CREATING"))
        container_id: str | None = None
        try:
            with operation("start_pod", node_id=self.node_id, pod_id=pod_id) as ctx:
                self.runtime.pull_image(spec["image"])
                labels = {
                    LABEL_MANAGED: "true",
                    LABEL_POD_ID: pod_id,
                    LABEL_POD_NAME: a["name"],
                    LABEL_EPOCH: str(a["restart_epoch"]),
                }
                container_id = self.runtime.create_container(
                    ContainerSpec(
                        name=f"orch-{a['name']}-{a['restart_epoch']}",
                        image=spec["image"],
                        command=spec.get("command"),
                        args=spec.get("args", []),
                        env=spec.get("environment", {}),
                        ports=[p["container_port"] for p in spec.get("ports", [])],
                        labels=labels,
                        cpu=spec.get("cpu_request"),
                        memory_mb=spec.get("memory_request"),
                    )
                )
                ctx["container_id"] = container_id
                self.runtime.start_container(container_id)
                self._observe_running(a, self.runtime.inspect_container(container_id))
        except ImageNotFoundError as exc:
            self._fail_permanently(pod_id, "ImageNotFound", str(exc), container_id)
        except ContainerCreateError as exc:
            self._fail_permanently(pod_id, "ContainerCreateFailed", str(exc), container_id)
        except RuntimeFailure:
            raise  # transient: leave state alone, retry on the next sync

    def _fail_permanently(
        self, pod_id: str, reason: str, message: str, container_id: str | None
    ) -> None:
        if container_id:
            self.runtime.remove_container(container_id)
        self._report(
            pod_id,
            PodStatusReport(status="FAILED", reason=reason, message=message[:900], permanent=True),
        )

    def _observe_running(self, a: dict[str, Any], info: ContainerInfo) -> None:
        spec, pod_id = a["spec"], a["id"]
        hc = spec.get("health_check")
        healthy: bool | None = None
        failures = 0
        if hc:
            state = self._run_health_check(pod_id, hc, info)
            healthy, failures = state.healthy, state.failures
        threshold = hc.get("failure_threshold", 3) if hc else 0
        unhealthy_now = bool(hc) and failures >= threshold
        in_sync = (
            a["status"] in ("RUNNING", "UNHEALTHY")
            and a["ready"] == (True if healthy is None else healthy)
            and (a["status"] == "UNHEALTHY") == unhealthy_now
        )
        if in_sync:
            return
        self._report(
            pod_id,
            PodStatusReport(
                status="RUNNING",
                container_id=info.id,
                ports={str(k): v for k, v in info.ports.items()},
                healthy=healthy,
                consecutive_failures=failures,
            ),
        )

    def _run_health_check(
        self, pod_id: str, hc: dict[str, Any], info: ContainerInfo
    ) -> HealthState:
        state = self._health.setdefault(pod_id, HealthState())
        now = time.monotonic()
        if state.last_checked and now - state.last_checked < hc["interval"]:
            return state
        host_port = info.ports.get(hc["port"])
        ok = host_port is not None and self.health_checker.check_http(
            self.runtime.host, host_port, hc["path"], hc["timeout"]
        )
        state.last_checked, state.healthy = now, ok
        state.failures = 0 if ok else state.failures + 1
        return state

    def _remove(self, pod_id: str, info: ContainerInfo) -> None:
        with operation(
            "remove_container", node_id=self.node_id, pod_id=pod_id, container_id=info.id
        ):
            self.runtime.stop_container(info.id)
            self.runtime.remove_container(info.id)

    def _report(self, pod_id: str, report: PodStatusReport) -> None:
        assert self.node_id
        try:
            self.client.report(self.node_id, pod_id, report)
        except ControlPlaneError as exc:
            log.warning("status report rejected", extra={"pod_id": pod_id, "error": str(exc)})

    # ------------------------------------------------------------------ lifecycle
    def stop(self) -> None:
        self._stop.set()

    async def register_with_retry(self) -> bool:
        delays = backoff_delays(1.0, 2.0, 30.0, 6)
        attempt = 0
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.register)
                return True
            except (ControlPlaneUnreachable, ControlPlaneError, RuntimeFailure) as exc:
                delay = delays[min(attempt, len(delays) - 1)]
                attempt += 1
                log.warning("registration failed", extra={"error": str(exc), "retry_in": delay})
                await self._sleep(delay)
        return False

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)

    async def _loop(self, name: str, interval: float, func: Any) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(func)
            except (ControlPlaneUnreachable, ControlPlaneError, RuntimeFailure) as exc:
                log.warning("loop iteration failed", extra={"loop": name, "error": str(exc)})
            except Exception:
                log.exception("unexpected error", extra={"loop": name})
            await self._sleep(interval)

    async def run(self) -> None:
        """Runs until stop() is called. Containers keep running on shutdown by design (like a
        kubelet restart): the next agent start re-adopts them by label."""
        if not await self.register_with_retry():
            return
        await asyncio.gather(
            self._loop("heartbeat", self.heartbeat_interval, self.heartbeat_once),
            self._loop("sync", self.sync_interval, self.sync_once),
        )

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterator

import httpx
import uvicorn

from orchestrator.common.logging import configure_logging
from worker.agent.agent import WorkerAgent
from worker.agent.client import ControlPlaneClient
from worker.agent.server import create_agent_app
from worker.config import WorkerSettings
from worker.health.checker import HttpHealthChecker
from worker.runtime.docker_runtime import DockerRuntime

log = logging.getLogger("worker")


class _Server(uvicorn.Server):
    """uvicorn without its own signal handling; the agent coordinates shutdown itself."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def run() -> None:
    settings = WorkerSettings()
    configure_logging(settings.log_level)
    runtime = DockerRuntime()
    key = settings.worker_api_key.get_secret_value()
    http = httpx.Client(
        base_url=settings.control_plane_url,
        timeout=settings.request_timeout,
        headers={"Authorization": f"Bearer {key}"},
    )
    agent = WorkerAgent(settings, ControlPlaneClient(http), runtime, HttpHealthChecker())

    config = uvicorn.Config(
        create_agent_app(runtime, key),
        host="0.0.0.0",  # noqa: S104 - the agent must be reachable from the control plane
        port=settings.agent_port,
        log_config=None,
    )
    server = _Server(config)

    loop = asyncio.get_running_loop()

    def shutdown() -> None:
        log.info("shutdown signal received")
        agent.stop()
        server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown)

    await asyncio.gather(agent.run(), server.serve())
    http.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

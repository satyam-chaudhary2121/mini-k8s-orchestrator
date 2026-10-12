"""Real OS process, real sockets: uvicorn API + background controllers + agents + CLI.

Only the container runtime is in-memory (no Docker daemon needed); everything else is the
production code path, including the asyncio Supervisor, timers, SIGTERM shutdown and the CLI.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from cli.main import app as cli_app
from tests.support.fake_runtime import FakeHealthChecker, FakeRuntime
from worker.agent.agent import WorkerAgent
from worker.agent.client import ControlPlaneClient
from worker.config import WorkerSettings

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "nginx.yaml"
ADMIN, WORKER = "live-admin", "live-worker"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class AgentThread:
    def __init__(self, url: str, hostname: str) -> None:
        self.runtime, self.health = FakeRuntime(host=hostname), FakeHealthChecker()
        settings = WorkerSettings(
            _env_file=None,
            control_plane_url=url,
            worker_api_key=WORKER,
            node_hostname=hostname,
            advertise_ip=hostname,
            node_cpu=2,
            node_memory_mb=2048,
        )
        http = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {WORKER}"})
        self.agent = WorkerAgent(settings, ControlPlaneClient(http), self.runtime, self.health)
        self.thread = threading.Thread(target=lambda: asyncio.run(self.agent.run()), daemon=True)

    def start(self) -> None:
        self.thread.start()

    def kill(self) -> None:  # abrupt "machine death": no more heartbeats or syncs
        self.agent.stop()
        self.thread.join(timeout=5)


@pytest.fixture
def live_api(postgres, tmp_path):
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": postgres,
        "ADMIN_API_KEY": ADMIN,
        "WORKER_API_KEY": WORKER,
        "API_HOST": "127.0.0.1",
        "API_PORT": str(port),
        "LOG_LEVEL": "INFO",
        "HEARTBEAT_INTERVAL": "1",
        "SYNC_INTERVAL": "0.3",
        "NODE_UNHEALTHY_AFTER": "2",
        "NODE_TIMEOUT": "4",
        "RECONCILIATION_INTERVAL": "0.3",
        "RESTART_BACKOFF_BASE": "0.1",
    }
    from sqlalchemy import create_engine

    from orchestrator.persistence.models import Base

    engine = create_engine(postgres)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    log_file = (tmp_path / "api.log").open("w")
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "orchestrator.api"],
        env=env,
        cwd=ROOT,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/ready", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("API server did not become ready")
    yield url, proc, tmp_path / "api.log"
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)


def _wait(predicate, timeout=20.0, msg="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {msg}")


def test_cluster_end_to_end_in_a_real_process(live_api):
    url, proc, log_path = live_api
    runner = CliRunner()
    base = ["--url", url, "--api-key", ADMIN]

    def cli(*args):
        result = runner.invoke(cli_app, [*base, *args])
        assert result.exit_code == 0, result.output
        return result.output

    w1, w2 = AgentThread(url, "worker-1"), AgentThread(url, "worker-2")
    w1.start()
    w2.start()
    _wait(lambda: cli("nodes", "list").count("READY") == 2, msg="two ready nodes")

    assert "deployment/nginx applied" in cli("deploy", str(EXAMPLE))
    assert "deployment/nginx applied" in cli("deploy", str(EXAMPLE))  # re-apply is idempotent
    _wait(lambda: cli("pods", "list").count("RUNNING") == 3, msg="3 running pods")
    out = cli("pods", "list")
    assert "worker-1" in out and "worker-2" in out

    _wait(
        lambda: len(
            httpx.get(
                f"{url}/api/v1/services/nginx", headers={"Authorization": f"Bearer {ADMIN}"}
            ).json()["endpoints"]
        )
        == 3,
        msg="3 service endpoints",
    )
    assert "scaled to 5" in cli("scale", "nginx", "--replicas", "5")
    _wait(lambda: cli("pods", "list").count("RUNNING") == 5, msg="5 running pods")

    victim = w1.runtime.running()[0]
    w1.runtime.kill(victim, exit_code=1)  # container crash
    _wait(
        lambda: victim not in w1.runtime.containers and cli("pods", "list").count("RUNNING") == 5,
        msg="crashed container replaced",
    )
    assert "1" in cli("events", "--type", "POD_RESTARTED")

    w1.kill()  # worker dies
    _wait(lambda: "OFFLINE" in cli("nodes", "list"), timeout=25, msg="worker-1 offline")
    _wait(
        lambda: cli("pods", "list").count("RUNNING") == 5 and "worker-1" not in cli("pods", "list"),
        timeout=25,
        msg="workloads rescheduled onto worker-2",
    )
    assert len(w2.runtime.running()) == 5

    metrics = httpx.get(f"{url}/metrics").text
    assert "nodes_offline 1.0" in metrics and "pods_running 5.0" in metrics
    assert 'reconciliation_total{controller="scheduler"}' in metrics
    w2.kill()

    # graceful shutdown: SIGTERM stops loops and exits cleanly
    proc.send_signal(signal.SIGTERM)
    # uvicorn re-raises the signal after a clean shutdown, so the exit status is 0 or -SIGTERM;
    # SIGKILL (-9) or a timeout would mean the shutdown hung.
    assert proc.wait(timeout=15) in (0, -signal.SIGTERM)
    log = log_path.read_text()
    assert "control plane stopping" in log and "Traceback" not in log

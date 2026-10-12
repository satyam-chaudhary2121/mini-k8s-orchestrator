"""Exercises the real DockerRuntime against a live daemon. Skipped when Docker is unavailable
(CI runs it on a GitHub runner, which has Docker)."""

from __future__ import annotations

import time
import uuid

import pytest

pytestmark = pytest.mark.docker

docker = pytest.importorskip("docker")


@pytest.fixture(scope="module")
def runtime():
    try:
        docker.from_env().ping()
    except Exception:
        pytest.skip("Docker daemon not reachable")
    from worker.runtime.docker_runtime import DockerRuntime

    return DockerRuntime(attempts=2, retry_base=0.1)


def test_container_lifecycle(runtime):
    from worker.runtime.base import ContainerSpec

    runtime.pull_image("busybox:latest")
    name = f"orch-test-{uuid.uuid4().hex[:8]}"
    cid = runtime.create_container(
        ContainerSpec(
            name=name,
            image="busybox:latest",
            command=["sh", "-c", "echo hello; sleep 30"],
            labels={"orchestrator.managed": "true", "orchestrator.test": name},
            memory_mb=64,
            cpu=0.1,
        )
    )
    try:
        runtime.start_container(cid)
        time.sleep(1)
        info = runtime.inspect_container(cid)
        assert info.status == "running" and info.labels["orchestrator.test"] == name
        assert "hello" in runtime.get_logs(cid)
        assert [c.id for c in runtime.list_containers({"orchestrator.test": name})] == [cid]
        runtime.stop_container(cid, timeout=1)
        assert runtime.inspect_container(cid).status == "exited"
    finally:
        runtime.remove_container(cid)
        runtime.remove_container(cid)  # idempotent
    assert runtime.list_containers({"orchestrator.test": name}) == []


def test_missing_image_is_a_permanent_error(runtime):
    from worker.runtime.base import ImageNotFoundError

    with pytest.raises(ImageNotFoundError):
        runtime.pull_image("registry.invalid.example/definitely/not-real:1")  # noqa: S105

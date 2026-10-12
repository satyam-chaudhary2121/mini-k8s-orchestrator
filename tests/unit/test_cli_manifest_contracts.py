from pathlib import Path

import pytest
from pydantic import ValidationError

from cli.manifest import ManifestError, load_manifests, parse_memory, to_request
from contracts.schemas import DeploymentCreate, ServiceCreate
from orchestrator.discovery.balancer import Endpoint, RoundRobinBalancer
from orchestrator.security.auth import Permission, Principal, Role

EXAMPLE = Path(__file__).parents[2] / "examples" / "nginx.yaml"


def test_example_manifest_converts_to_valid_api_bodies():
    docs = load_manifests(EXAMPLE.read_text())
    path, body = to_request(docs[0])
    assert path == "/api/v1/deployments"
    dep = DeploymentCreate.model_validate(body)
    assert dep.name == "nginx" and dep.replicas == 3 and dep.cpu_request == 0.25
    assert dep.memory_request == 256 and dep.health_check.failure_threshold == 3
    path, body = to_request(docs[1])
    assert path == "/api/v1/services" and ServiceCreate.model_validate(body).target_port == 80


def test_parse_memory_units():
    assert parse_memory(256) == 256 and parse_memory("256Mi") == 256 and parse_memory("1Gi") == 1024
    with pytest.raises(ManifestError):
        parse_memory("lots")


def test_manifest_errors():
    with pytest.raises(ManifestError, match="apiVersion"):
        to_request({"apiVersion": "v2", "kind": "Deployment"})
    with pytest.raises(ManifestError, match="unsupported kind"):
        to_request({"apiVersion": "orchestrator/v1", "kind": "Pod"})
    with pytest.raises(ManifestError, match="missing field"):
        to_request({"apiVersion": "orchestrator/v1", "kind": "Deployment", "spec": {}})


@pytest.mark.parametrize("name", ["Nginx", "-x", "x-", "a_b", "", "a" * 54])
def test_invalid_names_rejected(name):
    with pytest.raises(ValidationError):
        DeploymentCreate(name=name, image="x")


def test_health_check_port_must_be_exposed():
    with pytest.raises(ValidationError):
        DeploymentCreate(
            name="x", image="y", ports=[{"container_port": 80}], health_check={"port": 81}
        )
    with pytest.raises(ValidationError):
        DeploymentCreate(name="x", image="y", cpu_request=0)


def test_rbac_matrix():
    admin, op = Principal("a", Role.ADMIN), Principal("o", Role.OPERATOR)
    ro, worker = Principal("r", Role.READ_ONLY), Principal("w", Role.WORKER)
    assert all(admin.can(p) for p in Permission)
    assert op.can(Permission.DEPLOYMENTS_WRITE) and op.can(Permission.NODES_OPERATE)
    assert not op.can(Permission.NODES_ADMIN) and not op.can(Permission.AGENT)
    assert ro.can(Permission.PODS_READ) and not ro.can(Permission.DEPLOYMENTS_WRITE)
    assert worker.can(Permission.AGENT) and not worker.can(Permission.NODES_READ)


def test_round_robin_cycles_and_handles_empty():
    eps = [Endpoint(f"p{i}", f"pod{i}", "h", 80 + i) for i in range(3)]
    rr = RoundRobinBalancer()
    assert [rr.pick("s", eps).pod_id for _ in range(6)] == ["p0", "p1", "p2"] * 2
    assert rr.pick("s", []) is None
    assert rr.pick("other", eps).pod_id == "p0"  # counters are per service

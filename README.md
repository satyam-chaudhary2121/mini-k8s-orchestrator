# mini-k8s-orchestrator

A Kubernetes-inspired container orchestrator written in Python: a REST control plane (scheduler +
reconciling controllers + PostgreSQL state) and worker agents that run real Docker containers, with service
discovery, health checks, failure recovery, RBAC, metrics, a CLI, tests, Compose, Kubernetes manifests and CI.

> **Educational project - not production Kubernetes.** See [Limitations](#21-limitations).

## 1-2. Overview and architecture

```
 CLI / REST client
        |
        v
 +---------------------------- control plane (one process) ----------------------------+
 |  API server (FastAPI, auth/RBAC, idempotency)                                        |
 |  Scheduler | Deployment ctrl | Pod ctrl | Node ctrl | Endpoint ctrl   (Supervisor)    |
 +------------------------------------------+-------------------------------------------+
                                            |
                                     PostgreSQL (state, events, allocations)
                                            ^
            register / heartbeat / assignments / status (HTTP, pull model)
         +----------------------+----------------------+
   Worker agent 1         Worker agent 2          ...
   (owns Docker 1)        (owns Docker 2)
```
More: [`docs/architecture/`](docs/architecture) (system design, data flow, failure model, security,
observability, 10 Mermaid diagrams) and ADRs in [`docs/decisions/`](docs/decisions).

## 3. Why this exists
To demonstrate, with working code, how orchestrators work: desired-state reconciliation, scheduling with
resource accounting, failure detection and recovery, idempotent APIs and concurrency control.

## 4. Stack
Python 3.12, FastAPI, Pydantic, SQLAlchemy 2 + Alembic, PostgreSQL 16, Docker SDK, Typer (CLI), Prometheus +
Grafana, pytest, Ruff/Black/mypy, Docker Compose, Kubernetes YAML, GitHub Actions + Trivy.
Redis is intentionally **not** used ([ADR 0001](docs/decisions/0001-postgres-only-no-redis.md)).

## 5-6. Components
API server, scheduler, deployment/pod/node/endpoint controllers, worker agent, `ContainerRuntime` /
`DockerRuntime`, CLI. Interfaces: `SchedulerStrategy`, `ContainerRuntime`, `ServiceDiscovery`,
`LoadBalancer`, `HealthChecker`, `AuthenticationProvider`, `Database` (state store).

## 7. Scheduling
Filter (READY, not cordoned, selector, free CPU/memory) -> strategy (`first_fit`, `least_loaded`,
`resource_aware`) -> atomic bind under a node row lock that inserts a `resource_allocations` row. Unplaceable
pods stay `PENDING` with an explanation.

## 8. Reconciliation
Level-triggered loops compare desired and actual state every `RECONCILIATION_INTERVAL`, idempotently, with
row locks, exponential backoff on failure and graceful shutdown.

## 9-10. Workers and failure recovery
Agents register, heartbeat, poll their assignments and converge local Docker state, running HTTP health
checks locally. Container crash -> restart with backoff. Missed heartbeats -> UNHEALTHY (30 s) -> OFFLINE
(60 s) -> pods replaced on healthy nodes. Details: [failure model](docs/architecture/failure-model.md).

## 11. API
[`docs/api/api-reference.md`](docs/api/api-reference.md), and `/docs` / `/redoc` when running.

## 12-14. Install, develop, run with Compose
```bash
cp .env.example .env     # replace every CHANGE_ME (keys: python -c "import secrets;print(secrets.token_urlsafe(32))")
docker compose up --build
pip install -e .         # the CLI
export ORCHESTRATOR_URL=http://localhost:8000 ORCHESTRATOR_API_KEY=<ADMIN_API_KEY from .env>
```
Development and test instructions: [`docs/development/development.md`](docs/development/development.md).

## 15-17. Demo: deploy, scale, worker failure
```bash
orchestrator nodes list
orchestrator deploy examples/nginx.yaml
orchestrator pods list --deployment nginx          # NAME  NODE  STATUS  READY ...
orchestrator services get nginx                    # healthy endpoints
orchestrator scale nginx --replicas 5
docker compose stop worker-1 dind-1                # kill a worker (agent AND its Docker daemon)
orchestrator nodes list                            # worker-1 -> UNHEALTHY -> OFFLINE
orchestrator pods list --deployment nginx          # all 5 pods RUNNING on worker-2
```
`./scripts/demo.sh` runs this whole acceptance scenario with assertions; `scripts/chaos/run.sh` runs the
chaos cases.

## 18. Monitoring
Prometheus `:9090`, Grafana `:3000` (dashboard auto-provisioned), raw metrics at `/metrics`. Metric list:
[`docs/architecture/observability.md`](docs/architecture/observability.md).

## 19. Testing
```bash
pytest -m "not docker"      # needs PostgreSQL via TEST_DATABASE_URL
pytest -m docker            # DockerRuntime against a real daemon
```
Layers: unit, integration (API + Postgres, migrations), e2e (full control plane + agents, plus a test that
boots the real uvicorn process, agents and the CLI), chaos (concurrency, idempotency, DB outage).

## 20. Security
API keys (hashed at rest) + RBAC (`ADMIN`, `OPERATOR`, `READ_ONLY`, `WORKER`), secrets from the environment,
non-root image. **The Docker socket/daemon is root-equivalent** - read
[`docs/architecture/security.md`](docs/architecture/security.md).

## 21. Limitations
Single control plane (no leader election); no CNI/overlay networking - services are a resolver over published
host ports; no rolling updates (spec change => 409); no TLS out of the box; one shared worker key; HTTP health
checks and TCP ports only; single-container pods; no OpenTelemetry tracing; offset pagination.

## 22. Future improvements
Leader election + HA, watch/streaming protocol (gRPC), rolling updates, per-node mTLS credentials,
containerd runtime, event-driven (queue) reconciliation, namespaces/quotas, OpenTelemetry.

---

## Verification status (read this)

Verified by actually running them (PostgreSQL 16 in the build sandbox):
* 93 tests pass (unit, API/integration, Alembic migrations vs models, e2e scenarios, chaos/concurrency,
  and a live-process test: real uvicorn server + background controllers + agents over HTTP + CLI, worker
  death/reschedule, metrics, SIGTERM shutdown). `ruff`, `black --check` and `mypy` on the source packages
  are clean.

**Written but NOT executed** (the sandbox has no Docker daemon):
* `DockerRuntime` against a real daemon (`tests/integration/test_docker_runtime.py` is skipped without
  Docker; the CI `tests` job runs it).
* `docker compose up --build`, the Dockerfile builds, `scripts/demo.sh`, `scripts/chaos/run.sh`.
* GitHub Actions workflow (YAML parses; never run), Trivy scans, Grafana dashboard rendering.
* Kubernetes manifests (parse as YAML; never applied to a cluster).

Run `docker compose up --build && ./scripts/demo.sh` first; expect to fix small environment issues there.

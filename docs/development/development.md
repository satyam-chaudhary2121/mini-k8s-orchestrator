# Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # then replace the CHANGE_ME values
# PostgreSQL for tests (any 16+ instance):
docker run -d --name pg -e POSTGRES_USER=orch -e POSTGRES_PASSWORD=orch -e POSTGRES_DB=orch_test -p 5432:5432 postgres:16-alpine
export TEST_DATABASE_URL=postgresql+psycopg2://orch:orch@localhost:5432/orch_test

make lint typecheck test        # ruff + black --check, mypy, pytest -m "not docker"
make test-docker                # DockerRuntime against your local daemon
```

## Test layers
* `tests/unit` - pure logic: scheduler strategies/filters, state machine, retry/backoff, supervisor, manifest
  translation, RBAC matrix, round-robin.
* `tests/integration` - API + PostgreSQL (auth, errors, pagination, registration, logs path), Alembic
  migrations vs models, `DockerRuntime` vs a real daemon (`-m docker`).
* `tests/e2e` - real control plane + agents (in-memory runtime): deploy/scale/failure/drain/scheduling
  limits, driven by explicit `Cluster.tick()` so they are deterministic; `test_live_process.py` launches the
  real uvicorn process with timers, agents over HTTP, the CLI, node death and SIGTERM.
* `tests/chaos` - concurrency, idempotency, DB-outage resilience.

`tests/support/fake_runtime.py` is a test double for `ContainerRuntime` only; everything else (API,
database, scheduler, controllers, agent, health logic) is production code.

## Adding a scheduler strategy
Subclass `SchedulerStrategy`, implement `select`, add it to `STRATEGIES`, add unit tests, set
`SCHEDULER_STRATEGY`.

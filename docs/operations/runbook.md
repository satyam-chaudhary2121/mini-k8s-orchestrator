# Operations runbook

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | required | `postgresql+psycopg2://user:pass@host/db` |
| `ADMIN_API_KEY` | required | other keys optional (`OPERATOR_`, `READ_ONLY_`, `WORKER_API_KEY`) |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | |
| `HEARTBEAT_INTERVAL` | 10 | seconds, told to workers at registration |
| `NODE_UNHEALTHY_AFTER` / `NODE_TIMEOUT` | 30 / 60 | heartbeat age thresholds (seconds) |
| `RECONCILIATION_INTERVAL` | 2 | controller loop period (seconds) |
| `SCHEDULER_STRATEGY` | `least_loaded` | `first_fit`, `least_loaded`, `resource_aware` |
| `RESTART_BACKOFF_BASE` / `RESTART_BACKOFF_MAX` | 2 / 300 | crash-loop backoff (seconds) |
| `POD_RETENTION_SECONDS` | 3600 | how long TERMINATED pods are kept |
| `MAX_REPLICAS` | 500 | per deployment |
| `LOG_LEVEL` | `INFO` | |

Worker: `CONTROL_PLANE_URL`, `WORKER_API_KEY`, `NODE_HOSTNAME`, `ADVERTISE_IP`, `AGENT_URL`, `AGENT_PORT`,
`NODE_CPU`, `NODE_MEMORY_MB`, `NODE_LABELS` (JSON), plus Docker's own `DOCKER_HOST`.

## Migrations
`alembic upgrade head` (the API image does this on start). After changing models:
`alembic revision --autogenerate -m "..." ` then review; `tests/integration/test_migrations.py` fails if
models and migrations drift.

## Common situations
* **Pod stuck `PENDING`**: `orchestrator pods list`, then `GET /pods/{name}` -> `message` says which filter
  rejected each node (e.g. `insufficient cpu`).
* **Pod `FAILED` with `ImageNotFound`**: permanent; fix the image and re-create the deployment. It is not
  retried and does not churn.
* **Node `OFFLINE`**: its pods were replaced. When the agent returns it removes leftover containers.
* **Planned maintenance**: `orchestrator nodes drain worker-1`; when `drain_completed_at` is set the node is
  empty; `uncordon` afterwards.
* **Control plane restart**: safe; all state is in PostgreSQL. SIGTERM stops loops, then closes the pool.

## Chaos scenarios

| # | Scenario | How | Expected |
|---|---|---|---|
| 1 | Kill a container | `scripts/chaos/run.sh kill-container` | pod FAILED -> restarted (`POD_RESTARTED`), replicas restored |
| 2 | Stop a worker | `scripts/chaos/run.sh stop-worker` | UNHEALTHY at 30 s, OFFLINE at 60 s, pods recreated on the other worker |
| 3 | Disconnect a worker | `scripts/chaos/run.sh disconnect-worker` | same as 2; on reconnect orphaned containers are removed |
| 4 | Restart PostgreSQL | `scripts/chaos/run.sh restart-postgres` | `/ready` 503 meanwhile, loops back off, then recover; pods unaffected |
| 5 | Duplicate deployment requests | `tests/chaos` | exactly one deployment; replays carry `Idempotent-Replayed` |
| 6 | Scale rapidly / concurrently | `tests/chaos` | converges to the last writer's value, no duplicate pods |
| 7 | More workloads than capacity | `tests/e2e/test_scheduling_limits.py` | extra pods stay PENDING, no node over-committed |
| 8 | Invalid image | `tests/e2e/test_failure_recovery.py` | FAILED/ImageNotFound, no restart loop, capacity released |
| 9 | Insufficient resources | `tests/e2e/test_scheduling_limits.py` | PENDING with an explanation; schedules when capacity arrives |

## Docker access
See `docs/architecture/security.md` ("Docker socket / daemon access").

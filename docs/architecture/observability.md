# Observability

**Logs**: one JSON object per line (`orchestrator/common/logging.py`). API requests log `request_id`,
`timestamp`, `method`, `path`, `status`, `latency_ms`. Worker operations log `node_id`, `pod_id`,
`container_id`, `operation`, `result`, `duration_ms` (`worker/reporting/oplog.py`). Domain events are also
logged with `event_type`, `object_kind`, `object_id`, `event_payload`.

**Events**: append-only `events` table (`GET /api/v1/events`): `NODE_REGISTERED`, `NODE_UNHEALTHY`,
`NODE_OFFLINE`, `DEPLOYMENT_CREATED/SCALED`, `POD_SCHEDULED`, `POD_UNSCHEDULABLE`, `POD_STARTED`,
`POD_FAILED`, `POD_RESTARTED`, `POD_DELETED`, ... Written in the same transaction as the change they
describe, so the audit trail can never disagree with state.

**Metrics** (`GET /metrics`, unauthenticated - restrict at the network layer):

| Metric | Type | Notes |
|---|---|---|
| `orchestrator_requests_total{method,path,status}` | counter | `path` is the route template (bounded cardinality) |
| `orchestrator_request_duration_seconds{method,path}` | histogram | |
| `nodes_total / nodes_ready / nodes_unhealthy / nodes_offline` | gauge | computed from the DB at scrape time |
| `pods_total / pods_running / pods_failed / pods_pending` | gauge | `pods_total` excludes TERMINATED |
| `pods_restarts_total` | counter | |
| `scheduler_attempts_total / scheduler_failures_total` | counter | |
| `reconciliation_total{controller}` / `reconciliation_failures_total{controller}` | counter | per loop |

Gauges are computed at scrape time rather than maintained incrementally, so they cannot drift from
persisted state. Grafana dashboard: `monitoring/grafana/dashboards/orchestrator.json` (provisioned
automatically by Compose).

**Not implemented**: OpenTelemetry tracing. Reason: logs + metrics + events cover the debugging needs of a
single-process control plane; trace context would need propagation through the agent protocol. To add it:
instrument FastAPI/httpx/SQLAlchemy with `opentelemetry-instrumentation-*` and propagate `traceparent` in
the agent client.

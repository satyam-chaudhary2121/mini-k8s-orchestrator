# API reference

Interactive docs: `/docs` (Swagger UI) and `/redoc`. Base path `/api/v1`. All JSON.

**Authentication**: `Authorization: Bearer <key>` (or `X-API-Key: <key>`). Missing -> `401 MISSING_CREDENTIALS`,
wrong -> `401 INVALID_CREDENTIALS`, insufficient role -> `403 PERMISSION_DENIED`.
`/health`, `/ready`, `/metrics` are unauthenticated.

**Request IDs**: send `X-Request-ID` or one is generated; it is echoed in the response header and in every
error body.

**Error format** (all errors):
```json
{"error": {"code": "NODE_NOT_FOUND", "message": "node 'x' does not exist", "request_id": "..."}}
```
Validation errors (`422 VALIDATION_ERROR`) add `details: [{"field": "name", "message": "..."}]`.

**Pagination**: list endpoints take `limit` (1-500, default 100) and `offset`, and return
`{"items": [...], "total": n, "limit": n, "offset": n}`.

**Idempotency**: `POST /deployments` and `POST /deployments/{name}/scale` honour `Idempotency-Key`.
A retry with the same key and body replays the stored response with `Idempotent-Replayed: true`; same key
with a different body -> `422 IDEMPOTENCY_KEY_REUSED`; an in-flight duplicate -> `409 IDEMPOTENCY_IN_PROGRESS`
(only possible across transactions that have not committed). Failed requests do not consume the key.

## Roles

| Role | Can |
|---|---|
| ADMIN | everything (incl. `DELETE /nodes/{id}`) |
| OPERATOR | read all; create/delete deployments, pods, services; scale; drain/uncordon |
| READ_ONLY | all `GET` endpoints (incl. logs and events) |
| WORKER | register, heartbeat, assignments, pod status reports only |

## Nodes

| Method & path | Role | Notes |
|---|---|---|
| `POST /nodes/register` | WORKER | idempotent by `hostname`; returns `{node_id, heartbeat_interval, sync_interval}` |
| `GET /nodes` | read | `?status=READY`; includes `cpu_allocated`, `memory_allocated_mb`, `pod_count` |
| `GET /nodes/{id-or-hostname}` | read | |
| `DELETE /nodes/{id}` | ADMIN | `409 NODE_NOT_EMPTY` unless `?force=true` |
| `POST /nodes/{id}/drain` | OPERATOR | cordons and terminates its pods; `drain_completed_at` is set when empty |
| `POST /nodes/{id}/uncordon` | OPERATOR | |
| `POST /nodes/{id}/heartbeat` | WORKER | body `{runtime_info?, running_containers?}`; recovers UNHEALTHY/OFFLINE nodes |
| `GET /nodes/{id}/assignments` | WORKER | pods the agent must run/tear down |
| `PUT /nodes/{id}/pods/{pod_id}/status` | WORKER | observed state; `409 POD_NOT_ON_NODE` for foreign pods |

Register request:
```json
{"hostname": "worker-1", "ip": "10.0.0.10", "cpu": 4, "memory": 8192, "labels": {"zone": "a"},
 "agent_url": "http://10.0.0.10:9000"}
```
Response: `{"node_id": "...", "heartbeat_interval": 10, "sync_interval": 3.0}`

## Deployments

| Method & path | Role | Notes |
|---|---|---|
| `POST /deployments` | OPERATOR | `201` created, `200` identical spec already exists, `409 DEPLOYMENT_ALREADY_EXISTS` different spec |
| `GET /deployments`, `GET /deployments/{name}` | read | `desired_replicas`, `current_replicas`, `ready_replicas` |
| `DELETE /deployments/{name}` | OPERATOR | `202`; pods are terminated, then the deployment is removed |
| `POST /deployments/{name}/scale` | OPERATOR | body `{"replicas": n}` (0..`MAX_REPLICAS`) |

```json
{"name": "nginx", "image": "nginx:alpine", "replicas": 3, "cpu_request": 0.25, "memory_request": 256,
 "ports": [{"container_port": 80, "protocol": "TCP"}], "environment": {}, "command": null, "args": [],
 "restart_policy": "Always", "labels": {}, "node_selector": {},
 "health_check": {"type": "http", "path": "/", "port": 80, "interval": 10, "timeout": 3, "failure_threshold": 3}}
```
`memory_request` is MiB. `health_check.port` must be a declared container port. `name` must match
`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$` (<= 53 chars). Unknown fields are rejected. Changing the template of an
existing deployment is not supported (no rolling updates).

## Pods

| Method & path | Role | Notes |
|---|---|---|
| `GET /pods` | read | filters: `status`, `node`, `deployment`, `include_terminated`; TERMINATED hidden by default |
| `GET /pods/{id-or-name}` | read | |
| `DELETE /pods/{id-or-name}` | OPERATOR | `202`; owning deployment creates a replacement |
| `GET /pods/{id-or-name}/logs?tail=100` | read | `text/plain`; proxied to the node's agent. `409 LOGS_UNAVAILABLE`, `502 AGENT_UNREACHABLE` |

## Services

| Method & path | Role | Notes |
|---|---|---|
| `POST /services` | OPERATOR | `{"name","selector","port","target_port"}`; `201`, `200` if identical, `409` otherwise |
| `GET /services`, `GET /services/{name}` | read | detail includes `endpoints: [{pod, pod_id, address, port}]` (ready pods only) |
| `DELETE /services/{name}` | OPERATOR | `204` |
| `GET /services/{name}/resolve` | read | round-robin endpoint; `503 NO_READY_ENDPOINTS` |

## Events and system

`GET /events?type=POD_FAILED&object_kind=pod&object_id=...` (newest first). `GET /health` (liveness),
`GET /ready` (DB reachable, else `503`), `GET /metrics` (Prometheus).

## Example

```bash
curl -H "Authorization: Bearer $ORCHESTRATOR_API_KEY" -H "Idempotency-Key: $(uuidgen)" \
  -X POST localhost:8000/api/v1/deployments -d '{"name":"web","image":"nginx:alpine","replicas":2}' \
  -H 'Content-Type: application/json'
```

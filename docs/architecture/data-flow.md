# Data flow

## Deploy
1. `orchestrator deploy nginx.yaml` -> CLI converts the manifest and `POST /api/v1/deployments` with an `Idempotency-Key`.
2. API authenticates (SHA-256 of key -> `api_keys`), checks `deployments:write`, validates the body (Pydantic).
3. In **one transaction**: idempotency row inserted, `deployments` row inserted (`desired_replicas = N`),
   `DEPLOYMENT_CREATED` event written. Response 201.
4. Deployment controller (next pass): locks the deployment row, sees `live = 0 < desired`, inserts N `pods`
   (`PENDING`).
5. Scheduler: per pod, filter -> strategy -> `_bind` (allocation row + `SCHEDULED`).
6. Agent (next sync, <= `sync_interval`): `GET assignments`, sees a `SCHEDULED` pod with no container for its
   epoch -> reports `CREATING`, pulls image, creates and starts the container, inspects published ports,
   runs the first health check, reports `RUNNING` with `ports` and `healthy`.
7. API applies the report through the state machine (`SCHEDULED->CREATING->RUNNING`), sets `ready`.
8. Endpoint controller adds `service_endpoints` rows for ready pods.

## Heartbeat
Agent `POST /nodes/{id}/heartbeat` every `heartbeat_interval` -> `last_heartbeat_at = now()` (server clock).
Node controller: age > `NODE_UNHEALTHY_AFTER` -> `UNHEALTHY`; age > `NODE_TIMEOUT` -> `OFFLINE` and workload
failure. Each node is handled in its own transaction **after taking its row lock and re-checking the age**,
so a heartbeat that commits first always wins.

## Scale
`POST /deployments/{name}/scale` locks the deployment row and updates `desired_replicas`. It never creates
pods itself; the controller converges. Scale-down chooses victims by value (FAILED, PENDING, ... RUNNING),
newest first, and sets `desired_state = TERMINATED`; the agent removes containers and reports `TERMINATED`,
which releases the allocation.

See `diagrams.md` for sequence diagrams of each flow.

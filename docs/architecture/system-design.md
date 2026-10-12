# System design

## Components

| Component | Where | Responsibility |
|---|---|---|
| API server | `orchestrator/api` | AuthN/Z (API keys + RBAC), validation, idempotency, request IDs, writes **desired state** |
| Scheduler | `orchestrator/scheduler` | Binds `PENDING` pods to nodes (filter -> strategy -> atomic bind) |
| Deployment controller | `orchestrator/controller/deployments.py` | Diffs desired replicas vs live pods; creates/removes pods |
| Pod controller | `orchestrator/controller/pods.py` | Termination, restart-with-backoff, pruning |
| Node controller | `orchestrator/controller/nodes.py` | Heartbeat staleness -> UNHEALTHY -> OFFLINE; failing workloads; drain completion |
| Endpoint controller | `orchestrator/controller/endpoints.py` | Keeps `service_endpoints` = ready, selector-matching pods |
| Worker agent | `worker/agent` | Owns Docker on one node; registers, heartbeats, converges containers to assignments, health checks |
| ContainerRuntime | `worker/runtime` | Interface; `DockerRuntime` is the only real implementation |
| State store | PostgreSQL via SQLAlchemy | Single source of truth |
| CLI | `cli` | Talks to the REST API only |

The control-plane components run as lifecycle-managed asyncio tasks in **one process** (`Supervisor`);
blocking DB work runs in worker threads.

## Core principles

1. **Spec vs status.** Users/API write `desired_replicas` / `desired_state`. Controllers and agents
   write `status`. Nothing else mutates desired state.
2. **Level-triggered reconciliation.** Each pass reads the whole picture and acts on the difference, so a
   missed event costs nothing and every action is idempotent.
3. **Pods are replaced, not moved.** A pod on a lost node is failed and terminated; the deployment
   controller creates a *new* pod, which the scheduler places. (Same as Kubernetes.)
4. **The worker pulls.** Agents poll `GET /nodes/{id}/assignments` and push observed status. The control
   plane never opens connections to Docker engines; it only calls an agent for log streaming.
5. **Resource accounting is a table.** `resource_allocations` is the only source of "allocated"
   capacity; the bind step inserts a row under a node row lock.

## Domain model and state machines

Node: `REGISTERING -> READY <-> UNHEALTHY -> OFFLINE`, plus `DRAINING` (cordoned).
Pod: see the table enforced in `orchestrator/services/pods.py` (`POD_TRANSITIONS`):

```
PENDING -> SCHEDULED -> CREATING -> RUNNING <-> UNHEALTHY
                |           |          |            |
                +-----------+----------+------------+--> FAILED -> SCHEDULED (restart) | TERMINATED
 any active state ----------------------------------------------> TERMINATING -> TERMINATED
 PENDING -> TERMINATED
```
Arbitrary transitions raise `INVALID_STATE_TRANSITION`; worker reports that do not advance the machine are
ignored (idempotent).

## Scheduling

1. **Filter** (`filter_nodes`): node `READY` and not cordoned, selector labels match, free CPU >= request,
   free memory >= request. Rejection reasons are aggregated into the pod's `Unschedulable: 0/3 nodes are
   available: ...` message.
2. **Score** with a `SchedulerStrategy` (`first_fit`, `least_loaded`, `resource_aware`; add one by
   subclassing and registering in `STRATEGIES`).
3. **Bind** (`_bind`): lock the node row, recompute allocated CPU/memory from `resource_allocations`,
   re-check capacity, insert the allocation, set `pod.node_id`, `PENDING -> SCHEDULED`. The re-check under
   the lock is what makes concurrent schedulers safe.

## Restart semantics

The control plane owns restart policy. A worker reports `FAILED` (container exited). The pod controller
applies the policy (`Always`, `OnFailure` with non-zero exit, `Never`) and `base * 2^restarts` backoff
(capped), then bumps `restart_epoch` and sets `SCHEDULED`. The agent creates a fresh container whenever it
sees no container labelled with the current epoch. Permanent failures (`ImageNotFound`,
`ContainerCreateFailed`) are never restarted and keep counting toward desired replicas, so a bad image
does not cause infinite pod churn.

## Service discovery and load balancing

A `Service` has a label selector and a target port. The endpoint controller materialises `ready` pods
(RUNNING, health check passing) into `service_endpoints` as `node.ip:published_host_port`; unhealthy pods
disappear from it. `GET /services/{name}/resolve` returns the next endpoint (round-robin, process-local
counter). This is a **client-side resolver**, not a kube-proxy data plane; no traffic passes through the
control plane.

## Limitations (by design)

Single control-plane instance (no leader election); no CNI/overlay networking (ports are published on the
node); no rolling updates; no TLS between components out of the box; shared worker credential;
offset-based pagination; HTTP health checks only; TCP port publishing only. See `failure-model.md` and
`docs/interview/tradeoffs.md`.

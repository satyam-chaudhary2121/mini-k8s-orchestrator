# Architecture questions

**Why use a controller (reconciliation loop)?**
Because the system's job is to make reality match intent, and reality changes without our involvement
(crashes, node loss). Edge-triggered code ("on crash, restart") breaks when an event is lost or processed
twice. A level-triggered controller recomputes `desired - actual` each pass, so it self-heals from missed
events, restarts, and partial failures, and every action is idempotent. Here the deployment controller
compares `desired_replicas` with live pods; the pod controller compares `desired_state` with `status`.

**Why separate the scheduler from the worker?**
Placement is a global decision (needs the whole cluster's capacity), execution is local (needs Docker on one
machine). Separating them lets the scheduler be a pure function over state (`filter_nodes` + strategy,
unit-testable without a DB) and keeps the root-equivalent Docker access confined to the agent, which owns
all container operations on its node.

**How does reconciliation work here?**
`Supervisor` runs each controller on an interval in its own task. A pass loads rows under a lock
(`FOR UPDATE SKIP LOCKED` for pods/deployments), computes the diff, mutates desired state or creates pods,
and commits events with the change. Exceptions are caught per loop: the delay becomes
`interval * 2^failures` (capped, with jitter) and resets after a success; SIGTERM stops loops gracefully.

**What happens when a worker dies?**
Heartbeats stop. After `NODE_UNHEALTHY_AFTER` (30 s) the node controller marks it `UNHEALTHY` (no new
pods); after `NODE_TIMEOUT` (60 s) it locks the node, re-checks the age, marks it `OFFLINE`, sets its pods
`FAILED/NodeLost` with `desired_state=TERMINATED` and releases their allocations. The deployment controller
sees `live < desired` and creates new pods; the scheduler places them on healthy nodes. Services drop the
dead endpoints on the next endpoint pass. If the node returns, its agent removes containers it is no longer
assigned (orphans).

**How does scheduling work?** Filter (READY, not cordoned, selector, free CPU/memory) -> strategy
(`first_fit`, `least_loaded`, `resource_aware`) -> bind under a node row lock with a capacity re-check, which
inserts a `resource_allocations` row. No fit leaves the pod `PENDING` with an explanatory message.

**How is resource capacity calculated?**
`free = capacity - SUM(resource_allocations)` per node, computed from the table (never a counter on the node
row), so releasing an allocation on any termination path automatically restores capacity. Requests, not
actual usage, are accounted (like Kubernetes requests); there is no overcommit and no limits enforcement
beyond Docker's `--cpus/--memory` from the request.

**How do you prevent duplicate deployments?**
Three layers: unique `deployments.name`; create is a no-op returning 200 for an identical spec and 409 for a
different one; and `Idempotency-Key` stores the response in the same transaction as the work, so a retried
POST replays instead of re-executing, and concurrent duplicates serialise on the unique index.

**Why PostgreSQL?** Transactions, row locks, unique constraints, JSONB for specs, a mature migration story,
and one consistency domain. See ADR 0001.

**When would Redis be necessary?** With multiple control-plane replicas: leader election leases, a shared
work queue, or a cross-replica rate limiter. Postgres advisory locks / `LISTEN/NOTIFY` can cover some of that;
Redis is justified when latency or throughput of coordination outgrows the database.

**How would you replace REST with gRPC for workers?**
`ControlPlaneClient` is the only place the agent speaks to the control plane, and the DTOs live in
`contracts/`. Define protobufs mirroring them; implement a streaming `WatchAssignments` (server push on
change) and a client-streaming status channel. The agent's reconcile logic is unchanged because it already
works from an assignment list.

**How would you implement leader election?**
A lease row (`holder`, `expires_at`) renewed every few seconds with `UPDATE ... WHERE holder = me OR
expires_at < now() RETURNING`, or `pg_try_advisory_lock` on a dedicated connection (released automatically
if the process dies). Only the leader runs `Supervisor` loops; all replicas serve the API. Fencing: include
a monotonically increasing lease epoch in writes that must not be applied by a stale leader.

**How would you secure worker <-> control-plane traffic?**
mTLS with per-node client certificates (identity = node), replacing the shared worker key; check that the
cert's node matches `{ident}` in every agent endpoint; short-lived tokens issued at registration; network
policy so agent ports are reachable only from the control plane.

**How does Kubernetes solve these differently?**
etcd (Raft) instead of PostgreSQL; watch streams instead of polling; per-resource `resourceVersion` for
optimistic concurrency; a pluggable CRI/CNI instead of Docker + published ports; kube-proxy/EndpointSlices
for service traffic; leader-elected controller managers; Node leases for cheap heartbeats; rolling updates
via ReplicaSets; admission control and RBAC with service accounts.

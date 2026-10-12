# Scaling discussion

## Where this design stops scaling (and why)
* **Polling load.** N agents x (heartbeat + assignments) every few seconds. At 1,000 nodes with 3 s sync
  that is ~330 req/s of mostly-empty reads.
* **Controller passes are O(pods + nodes)** and run in one process.
* **`node_views` aggregates all allocations** on each scheduling decision (O(nodes) per pod).
* **Single PostgreSQL primary** is the throughput and availability ceiling.
* **Offset pagination** gets slower with depth.

## Getting to ~1,000 nodes
1. **Cheap heartbeats**: a separate lightweight lease endpoint/table updated by `UPDATE ... SET ts = now()`
   (HOT updates, no event rows); keep full status reports change-driven only.
2. **Push instead of poll**: gRPC/SSE `WatchAssignments`, using a per-node `generation` counter or Postgres
   `LISTEN/NOTIFY`; agents fall back to slow resync (e.g. 30 s) for safety.
3. **Incremental scheduling**: cache node views in memory with invalidation on bind/release, score a random
   sample of feasible nodes (as Kubernetes does with `percentageOfNodesToScore`), batch binds.
4. **Shard controllers**: partition deployments/pods by hash across replicas, with leader election per shard
   (or `SKIP LOCKED` queues of "dirty" objects instead of scanning everything).
5. **Event-driven reconciliation**: enqueue the owning deployment when its pod changes; periodic full resync
   as the safety net.
6. **Database**: read replicas for GET traffic and metrics; partition `events` by time and expire; keyset
   pagination; PgBouncer in front.
7. **Metrics**: scrape-time DB gauges become a materialised snapshot or are maintained by the controller.

## Multi-tenancy
Add `namespace` (tenant) to every resource and unique constraint (`UNIQUE(namespace, name)`); scope API keys
to namespaces in RBAC; per-namespace quotas enforced in the bind step (`SUM(allocations) <= quota`);
label-based node pools per tenant; network isolation requires the missing CNI layer.

## High availability
Run N API replicas behind a load balancer (stateless already), elect one controller leader (lease), use
managed/replicated PostgreSQL with failover. Correctness already tolerates duplicate controller activity
because actions are idempotent and guarded by row locks.

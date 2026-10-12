# Design decisions (and the reasoning to say out loud)

1. **Desired vs actual state.** API only writes desired; controllers/agents only write status. Makes every
   component restartable and the system explainable.
2. **Pods are replaced, not moved** (ADR 0007). Single-assignment pods, simple state machine, matches K8s.
3. **Control plane owns restarts via an epoch** (ADR 0004). Restarts are auditable state, not Docker magic.
4. **Pull-based workers** (ADR 0003). No inbound connection to engines; restart-safe; the seam for gRPC.
5. **PostgreSQL only** (ADR 0001) and **sync SQLAlchemy** (ADR 0002).
6. **Permanent vs transient failures.** Runtime errors are classified (`RuntimeUnavailableError` retried
   with 1,2,4 s backoff; `ImageNotFoundError`/`ContainerCreateError` permanent). Permanent failures are not
   restarted and still count toward replicas, so a typo in an image name cannot create an infinite
   create/fail loop.
7. **Allocations as rows.** Capacity is derived, so no counter can drift; the unique `pod_id` constraint
   prevents double allocation.
8. **Events in the same transaction** as the change, so audit == state.
9. **Metrics gauges computed at scrape time** from the DB; counters for things that only exist as events.
10. **State machine as data** (`POD_TRANSITIONS`), unit-tested for reachability; invalid transitions raise.
11. **Test seam = `ContainerRuntime` only.** The in-memory runtime is the sole test double; this keeps e2e
    tests honest about API/DB/scheduler/controller/agent behaviour.
12. **Concurrency tools, each for a reason:** row locks (serialise writers of one row: scale, bind),
    `SKIP LOCKED` (multiple controller passes don't block each other), unique constraints (names,
    allocations, idempotency), advisory lock (startup bootstrap across replicas), transactions (atomic
    state+event+allocation). Optimistic concurrency (`version` columns) was not needed because contention
    per row is low and lock hold times are tiny; it would be the choice for long-lived, user-edited
    documents.

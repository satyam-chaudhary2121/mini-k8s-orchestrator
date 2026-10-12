# Failure scenarios - what to say

Each is backed by an automated test (see `docs/architecture/failure-model.md`).

* **Container crashes.** Agent observes `exited` -> reports `FAILED(exit_code)` -> pod controller applies
  restart policy and backoff -> epoch bump -> agent creates a new container -> `RUNNING`; the endpoint is
  removed while unhealthy and re-added when ready.
* **Worker dies / is partitioned.** Heartbeat timeout -> UNHEALTHY -> OFFLINE -> pods failed and replaced.
  Partition difference: the old containers keep running, so you briefly have at-least-once execution; on
  reconnect the agent deletes containers not in its assignments.
* **Duplicate API request.** Same `Idempotency-Key`: replay. Same name, same spec: 200 no-op. Concurrent
  duplicates: unique index serialises, exactly one executes.
* **Concurrent scale.** Row lock; last writer wins; controller converges; no duplicate pods (pod names are
  unique and creation is driven by diff, not by the API call).
* **Database restart.** Pool pre-ping reconnects; controller loops see `OperationalError`, back off, resume;
  `/ready` reports 503 so orchestrators stop routing; running containers are unaffected because agents keep
  them running independently.
* **Out-of-capacity.** Pod `PENDING`, with "0/2 nodes are available: 2 insufficient cpu"; becomes scheduled
  when capacity appears or something is scaled down. No overcommit even with racing schedulers.
* **Invalid image.** Permanent failure, no churn, capacity released, visible reason.
* **What is NOT handled:** control-plane HA, fencing of partitioned nodes, durable agent status queue.

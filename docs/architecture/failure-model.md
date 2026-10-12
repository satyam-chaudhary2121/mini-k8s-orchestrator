# Failure model

| Failure | Detection | Response | Test |
|---|---|---|---|
| Container crash | Agent sees exited container -> reports `FAILED` | Pod controller restarts per policy with backoff (new container, `restart_count++`) | `test_container_crash_is_restarted_and_endpoint_returns` |
| Crash loop | `restart_count` grows | Backoff `2^n` s capped at `RESTART_BACKOFF_MAX` | `test_restart_backoff_doubles_and_caps` |
| Bad image | `ImageNotFoundError` from runtime | Permanent `FAILED/ImageNotFound`; no restart; capacity released; no churn | `test_invalid_image_fails_permanently_without_churn` |
| Docker daemon down | `RuntimeUnavailableError` | Agent retries with backoff and leaves state alone; pod is not failed | `DockerRuntime._call` + retry tests |
| Worker dies | No heartbeat | `UNHEALTHY` (30 s) -> `OFFLINE` (60 s); pods `FAILED/NodeLost`, replaced elsewhere | `test_worker_failure_reschedules_workloads`, live-process test |
| Worker partitioned | Same as above | Replaced elsewhere; on reconnect the agent removes containers that are no longer assigned (orphans) | `test_returning_worker_cleans_up_orphaned_containers` |
| Duplicate request | `Idempotency-Key` / unique name | Replay stored response or no-op | `tests/chaos/test_concurrency_and_idempotency.py` |
| Concurrent scale | Row lock | Serialised; converges on last writer; never duplicates pods | `test_rapid_concurrent_scaling_...` |
| Concurrent schedulers | Node row lock + capacity re-check | Never over-commits a node | `test_concurrent_schedulers_never_overcommit_a_node` |
| Over-capacity request | Filter finds no node | Pod stays `PENDING` with an explanation; schedules when capacity appears | `test_more_workloads_than_cluster_capacity_...` |
| PostgreSQL restart | `pool_pre_ping`, `OperationalError` | `/ready` -> 503; controller loops back off exponentially and resume; API retries bootstrap at start | `test_controller_loop_recovers_after_database_errors`, `scripts/chaos/run.sh restart-postgres` |
| API server restart | - | State is in PostgreSQL; loops restart; agents retry transient errors | live-process test (SIGTERM) |
| Control-plane crash mid-transaction | Transactions | Atomic: either the whole change (incl. event, allocation) or none | by construction |

## Known gaps (not handled)

* **Split brain / HA**: one control plane. Running two replicas is *safe for correctness* (row locks,
  `SKIP LOCKED`, idempotent reconciliation) but not tested or supported; leader election would remove the
  duplicated work.
* **Zombie containers on a dead machine** keep running until the machine returns; the control plane cannot
  fence a node it cannot reach.
* **At-most-once is not guaranteed**: after `NodeLost`, replacements may run while the old containers still
  live on a partitioned node (at-least-once semantics, like Kubernetes without fencing).
* Worker status reports are retried on the next sync, not queued durably.

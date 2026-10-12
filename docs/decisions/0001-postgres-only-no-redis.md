# ADR 0001: PostgreSQL as the only state store (no Redis in v1)

**Status:** accepted

## Problem
The control plane needs durable state, transactions, row locking and uniqueness guarantees. Redis was suggested for locks, queues and caching.

## Options
1. PostgreSQL only. 2. PostgreSQL + Redis (locks/queues). 3. etcd-like KV store.

## Chosen solution
PostgreSQL only. `REDIS_URL` exists in settings but is unused.

## Reason
Every concurrency need is met by Postgres primitives: `SELECT ... FOR UPDATE [SKIP LOCKED]` (deployments, nodes, pods), unique constraints (names, allocations, idempotency keys), `pg_advisory_xact_lock` (startup bootstrap), and transactions that make state + event + allocation atomic. Adding Redis would add a second consistency domain with no benefit at this scale.

## Trade-offs
Polling controllers instead of a queue/watch mechanism (latency <= `RECONCILIATION_INTERVAL`); DB load grows with cluster size. Redis (or Postgres LISTEN/NOTIFY) becomes justified with multiple control-plane replicas needing leader election/leases or a work queue.

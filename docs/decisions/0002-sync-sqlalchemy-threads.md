# ADR 0002: Synchronous SQLAlchemy in worker threads instead of async SQLAlchemy

**Status:** accepted

## Problem
FastAPI is async-first, but the workload is transaction-heavy with row locks and multi-statement units of work.

## Options
1. Async SQLAlchemy + asyncpg. 2. Sync SQLAlchemy; FastAPI runs sync routes in a threadpool; controllers run `asyncio.to_thread`.

## Chosen solution
Sync SQLAlchemy 2.0 + psycopg2.

## Reason
Explicit, easy-to-reason-about transactions (`Database.transaction()`), simpler testing (the same services run from tests, controllers and routes), fewer async/greenlet pitfalls with `FOR UPDATE` and savepoints. Controllers are still lifecycle-managed asyncio tasks (`Supervisor`).

## Trade-offs
Thread pool bounds concurrency (pool size 10 + 10 overflow); not ideal for thousands of concurrent requests. Swapping to async is mechanical because DB access is confined to services/controllers.

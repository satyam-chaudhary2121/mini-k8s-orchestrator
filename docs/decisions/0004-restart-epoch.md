# ADR 0004: Control plane owns restarts via a restart epoch

**Status:** accepted

## Problem
Container crashes must be restarted per policy with backoff, and the decision must be visible and auditable.

## Options
1. Docker restart policies on the container. 2. Agent restarts locally. 3. Control plane decides; agent obeys a counter.

## Chosen solution
The pod controller applies policy/backoff and increments `restart_epoch`; the agent creates a new container whenever none carries the current epoch label.

## Reason
Restarts become first-class state (`restart_count`, events, metrics), work identically if a pod is rescheduled, and are idempotent: replaying the same epoch never double-starts.

## Trade-offs
A restart takes a controller tick + a sync tick (seconds) instead of milliseconds; if the control plane is down, crashed containers are not restarted.

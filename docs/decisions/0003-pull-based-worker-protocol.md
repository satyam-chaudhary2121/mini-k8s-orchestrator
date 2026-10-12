# ADR 0003: Workers pull assignments; the control plane never calls Docker

**Status:** accepted

## Problem
The API server must not directly manipulate every Docker engine (requirement), and workers can restart, sit behind NAT, or disappear.

## Options
1. Control plane pushes commands to agents over HTTP. 2. Agents poll assignments and push status (kubelet-style). 3. gRPC streaming.

## Chosen solution
Agents poll `GET /nodes/{id}/assignments` every `SYNC_INTERVAL` and report `PUT .../pods/{id}/status`.

## Reason
No control-plane -> worker connection state to manage; a restarted agent simply re-reads desired state and re-adopts containers by label; idempotent by construction; REST keeps the contract simple and replaceable by gRPC (the `ControlPlaneClient` is the seam).

## Trade-offs
Up to one sync interval of latency per action; N agents polling creates steady load. The control plane still calls an agent for log retrieval (needs reachability).

# Trade-offs and honest limitations

| Choice | Benefit | Cost |
|---|---|---|
| Polling controllers (2 s) | Simple, level-triggered, robust | Latency, DB load; no watch |
| PostgreSQL as store | Transactions, constraints, ops familiarity | Not a purpose-built consistent KV; scaling ceiling |
| Pull-based agents | Restart-safe, no inbound engine access | Up to `SYNC_INTERVAL` latency |
| Published host ports + resolver | Works with plain Docker | Not a service mesh; no cluster IPs/DNS; no traffic through the control plane |
| Round-robin counter in process | Trivial, real | Resets on restart; not shared across replicas |
| Shared worker API key | Simple to operate | A compromised worker can impersonate others |
| No rolling updates | Smaller state machine | Changing a spec means delete + create |
| Replace-not-move pods | Clean semantics | Pod history rows accumulate (pruned after 1 h) |
| Requests-only accounting | Predictable scheduling | No usage-based bin packing or overcommit |
| DinD in Compose | Realistic isolation | Privileged; dev-only |
| Offset pagination | Easy | Slow at depth; unstable under writes |
| Sync SQLAlchemy | Simple transactions | Thread-bound concurrency |

## Things this project deliberately does not claim
Not Kubernetes; no CNI/overlay network, no cluster DNS, no rolling updates, no TLS out of the box, no
control-plane HA, no OpenTelemetry tracing, no UDP port publishing, single-container pods only (the pod
spec/`containers` table are shaped to allow more).

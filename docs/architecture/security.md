# Security model

* **AuthN**: API keys (`Authorization: Bearer` or `X-API-Key`). Only SHA-256 digests are stored in
  `api_keys`; raw secrets come from the environment (`ADMIN_API_KEY`, `OPERATOR_API_KEY`,
  `READ_ONLY_API_KEY`, `WORKER_API_KEY`) and are synced at startup (rotate = change env + restart).
  The `AuthenticationProvider` interface allows JWT/OIDC to be added without touching routes.
* **AuthZ**: RBAC - `ADMIN` (all), `OPERATOR` (read + deployments/pods/services write + drain/uncordon),
  `READ_ONLY`, `WORKER` (register, heartbeat, assignments, status only). Enforced per route via
  `require(Permission)`; the matrix is unit-tested.
* **Errors** never leak internals: unhandled exceptions return `INTERNAL_ERROR` + `request_id`.
* **Secrets**: nothing hard-coded; `.env.example` has placeholders; Kubernetes secret is a template.
  Settings fail fast if `DATABASE_URL`/`ADMIN_API_KEY` are missing.
* **Control plane -> agent** log requests carry the worker key; the agent compares in constant time.
* **Containers**: the control-plane image runs as non-root (uid 10001); K8s manifest drops capabilities and
  uses a read-only root filesystem.

## Docker socket / daemon access (important)

A worker needs to control a Docker daemon, which is **root-equivalent on that daemon's host**.
* Compose uses one **Docker-in-Docker** daemon per worker (`privileged: true`, TLS off, reachable only on
  the private compose network). It isolates workloads per "node" and makes "stop worker" realistic, but
  `privileged` DinD is unsuitable for production.
* The Kubernetes DaemonSet mounts `/var/run/docker.sock`; anyone who can run that pod controls the node.
* Production alternatives: rootless Docker, a socket proxy that allow-lists API calls, or a different
  `ContainerRuntime` (containerd/Podman).

## Not implemented / gaps

TLS/mTLS between CLI, workers and API (terminate TLS at an ingress or add mTLS); per-node worker
credentials (all workers share one key, so a compromised worker can report for any node - the API only
checks that a pod belongs to the *named* node); JWT; rate limiting; audit of read access; API-key
management endpoints.

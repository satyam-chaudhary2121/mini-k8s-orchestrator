# Docker access for workers

See `docs/architecture/security.md`. Summary: each Compose worker talks to its own privileged
Docker-in-Docker daemon over the private network (`DOCKER_HOST=tcp://dind-N:2375`, TLS off). Do not publish
that port. In Kubernetes the DaemonSet mounts the host socket - equivalent to root on the node.

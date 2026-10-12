# ADR 0006: One Docker-in-Docker daemon per worker in Compose

**Status:** accepted

## Problem
Workers must really control Docker, and the failure demo ("stop a worker") must make that node's containers disappear.

## Options
1. All workers share the host socket. 2. One DinD daemon per worker. 3. Real VMs.

## Chosen solution
DinD per worker, TLS disabled on the private network, ports not published.

## Reason
With a shared socket, stopping a worker agent leaves its containers running and unrelated to "node" failure; DinD gives each node an isolated runtime and network namespace (`ADVERTISE_IP=dind-N`).

## Trade-offs
`privileged` containers; slower startup; image cache per node. Documented in security.md as unsuitable for production.

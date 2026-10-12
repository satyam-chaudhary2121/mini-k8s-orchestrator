# ADR 0007: Failed pods are replaced, never moved

**Status:** accepted

## Problem
When a node dies its pods must run elsewhere without ambiguous ownership.

## Options
1. Re-bind the same pod to a new node. 2. Terminate the old pod and create a new one.

## Chosen solution
Old pods go `FAILED/NodeLost` with `desired_state=TERMINATED`; the deployment controller creates fresh pods.

## Reason
Keeps pods single-assignment (a pod never has two nodes), keeps the state machine acyclic for node moves, and mirrors Kubernetes semantics. History is preserved for debugging.

## Trade-offs
Brief over-count of records; at-least-once execution during partitions (old containers may still run until the node returns and the agent removes them).

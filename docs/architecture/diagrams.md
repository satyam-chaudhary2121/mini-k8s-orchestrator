# Architecture diagrams (Mermaid)

## 1. Overall architecture
```mermaid
flowchart TB
  CLI[CLI / REST client] --> API[API server<br/>FastAPI, auth, RBAC]
  subgraph CP[Control plane process]
    API
    SCH[Scheduler]
    DC[Deployment controller]
    PC[Pod controller]
    NC[Node controller]
    EC[Endpoint controller]
  end
  API --> DB[(PostgreSQL)]
  SCH --> DB
  DC --> DB
  PC --> DB
  NC --> DB
  EC --> DB
  W1[Worker agent 1] -- register / heartbeat / assignments / status --> API
  W2[Worker agent 2] -- register / heartbeat / assignments / status --> API
  W1 --> D1[(Docker engine 1)]
  W2 --> D2[(Docker engine 2)]
  API -. logs .-> W1
  PROM[Prometheus] --> API
```

## 2. Deployment flow
```mermaid
sequenceDiagram
  participant U as CLI
  participant A as API
  participant DB as PostgreSQL
  participant DC as Deployment controller
  participant S as Scheduler
  participant W as Worker agent
  U->>A: POST /deployments (Idempotency-Key)
  A->>DB: tx: idempotency row + deployment + event
  A-->>U: 201
  DC->>DB: lock deployment, insert N PENDING pods
  S->>DB: bind pod -> node (allocation + SCHEDULED)
  W->>A: GET assignments
  W->>W: pull image, create + start container
  W->>A: PUT status RUNNING (ports, healthy)
  A->>DB: state machine: CREATING -> RUNNING, ready
```

## 3. Scheduling flow
```mermaid
flowchart LR
  P[PENDING pod] --> F{Filter nodes<br/>READY, not cordoned,<br/>selector, CPU, memory}
  F -- none --> U[Stay PENDING<br/>Unschedulable: why] --> P
  F -- candidates --> ST[Strategy picks node<br/>first_fit / least_loaded / resource_aware]
  ST --> L[Lock node row, recompute allocated]
  L --> C{Still fits?}
  C -- no --> U
  C -- yes --> B[Insert allocation, SCHEDULED]
```

## 4. Reconciliation loop
```mermaid
flowchart TD
  T[Tick every RECONCILIATION_INTERVAL] --> R[Load desired state + live pods]
  R --> D{desired - live}
  D -- "> 0" --> CR[Create pods]
  D -- "< 0" --> TM[Mark victims desired_state=TERMINATED]
  D -- "= 0" --> N[Nothing]
  CR --> OK
  TM --> OK
  N --> OK[Success: reset backoff]
  R -- exception --> BO[Backoff interval x 2^n, jitter] --> T
  OK --> T
```

## 5. Worker registration
```mermaid
sequenceDiagram
  participant W as Agent
  participant A as API
  W->>A: POST /nodes/register (hostname, ip, cpu, memory, labels)
  A->>A: upsert by hostname (idempotent)
  A-->>W: node_id, heartbeat_interval, sync_interval
  loop every heartbeat_interval
    W->>A: POST /nodes/{id}/heartbeat
  end
  Note over W,A: 404 NODE_NOT_FOUND -> agent re-registers
```

## 6. Heartbeat / failure detection
```mermaid
stateDiagram-v2
  [*] --> READY: register
  READY --> UNHEALTHY: age > NODE_UNHEALTHY_AFTER
  UNHEALTHY --> OFFLINE: age > NODE_TIMEOUT
  UNHEALTHY --> READY: heartbeat
  OFFLINE --> READY: heartbeat
  READY --> DRAINING: drain
  DRAINING --> READY: uncordon
```

## 7. Container failure recovery
```mermaid
sequenceDiagram
  participant W as Agent
  participant A as API
  participant PC as Pod controller
  W->>A: status FAILED (exit 1)
  PC->>PC: policy Always, backoff elapsed
  PC->>A: (DB) FAILED -> SCHEDULED, restart_epoch++
  W->>A: assignments (epoch changed)
  W->>W: remove old container, create new
  W->>A: status RUNNING
```

## 8. Worker failure recovery
```mermaid
sequenceDiagram
  participant N as Node controller
  participant DC as Deployment controller
  participant S as Scheduler
  participant W2 as Healthy agent
  N->>N: no heartbeat > NODE_TIMEOUT
  N->>N: node OFFLINE, pods FAILED/NodeLost, desired=TERMINATED
  DC->>DC: live < desired -> create replacement pods
  S->>S: bind to healthy node
  W2->>W2: start containers, report RUNNING
```

## 9. Service discovery
```mermaid
flowchart LR
  Pods[Pods RUNNING + ready] --> EC[Endpoint controller<br/>selector match, port_map]
  EC --> REG[(service_endpoints)]
  REG --> GET[GET /services/name]
  REG --> RES[GET /services/name/resolve<br/>round-robin]
  Unhealthy[Unhealthy or lost pod] -. removed on next pass .-> REG
```

## 10. Scaling
```mermaid
sequenceDiagram
  participant U as Client
  participant A as API
  participant DC as Deployment controller
  U->>A: POST /deployments/web/scale {replicas: 5}
  A->>A: lock row, set desired_replicas (no-op if equal)
  A-->>U: 200
  DC->>DC: diff -> create (up) or terminate victims (down)
  Note over A,DC: concurrent scale calls serialise on the row lock and converge
```

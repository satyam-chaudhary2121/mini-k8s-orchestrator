#!/usr/bin/env bash
# Chaos scenarios that need the real compose stack. Scenarios 5-9 (duplicate requests, rapid
# scaling, over-capacity, invalid image, insufficient resources) are automated in tests/chaos and
# tests/e2e. Usage: ./scripts/chaos/run.sh <kill-container|stop-worker|disconnect-worker|restart-postgres>
source "$(dirname "$0")/../lib.sh"
cd "$(dirname "$0")/../.."
orchestrator deploy examples/nginx.yaml >/dev/null
wait_for "3 pods RUNNING" 240 running_is nginx 3
case "${1:-}" in
  kill-container)       # expect: pod FAILED -> restarted, count back to 3
    docker compose exec -T dind-1 sh -c 'docker kill $(docker ps -q --filter label=orchestrator.managed=true | head -1)'
    wait_for "restarted and 3 RUNNING" 180 bash -c 'orchestrator events --type POD_RESTARTED | grep -q POD_RESTARTED && [[ $(orchestrator pods list --deployment nginx | grep -c " RUNNING ") == 3 ]]';;
  stop-worker)          # expect: node OFFLINE after NODE_TIMEOUT, pods recreated on the other worker
    docker compose stop worker-1 dind-1
    wait_for "worker-1 OFFLINE" 180 bash -c 'orchestrator nodes list | grep worker-1 | grep -q OFFLINE'
    wait_for "3 RUNNING on worker-2" 240 bash -c '[[ $(orchestrator pods list --deployment nginx | grep -c "worker-2.*RUNNING") == 3 ]]';;
  disconnect-worker)    # expect: control plane replaces pods; on reconnect the worker removes orphaned containers
    net=$(docker inspect "$(docker compose ps -q worker-1)" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
    docker network disconnect "$net" "$(docker compose ps -q worker-1)"
    wait_for "worker-1 OFFLINE" 180 bash -c 'orchestrator nodes list | grep worker-1 | grep -q OFFLINE'
    docker network connect "$net" "$(docker compose ps -q worker-1)"
    wait_for "worker-1 READY again" 180 bash -c 'orchestrator nodes list | grep worker-1 | grep -q READY'
    wait_for "3 pods RUNNING" 240 running_is nginx 3;;
  restart-postgres)     # expect: API reports /ready 503 meanwhile, controllers back off, then everything recovers
    docker compose restart postgres
    wait_for "control plane ready again" 120 curl -fsS "$ORCHESTRATOR_URL/ready"
    wait_for "3 pods still RUNNING" 120 running_is nginx 3;;
  *) die "usage: $0 <kill-container|stop-worker|disconnect-worker|restart-postgres>";;
esac
ok "chaos scenario '$1' recovered"

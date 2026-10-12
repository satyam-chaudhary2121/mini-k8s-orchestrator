#!/usr/bin/env bash
# End-to-end demonstration / acceptance run against `docker compose up --build`.
# Usage: ./scripts/demo.sh [--ci]    (reads ADMIN_API_KEY from the environment or .env)
source "$(dirname "$0")/lib.sh"
[[ -f .env && -z "${ADMIN_API_KEY:-}" ]] && { set -a; . ./.env; set +a; export ORCHESTRATOR_API_KEY="$ADMIN_API_KEY"; }
cd "$(dirname "$0")/.."

step "1-2. Cluster is up and both workers registered"
wait_for "control plane ready" 120 curl -fsS "$ORCHESTRATOR_URL/ready"
wait_for "2 nodes READY" 120 nodes_ready_is 2
orchestrator nodes list

step "3-5. Deploy nginx x3 (and a Service)"
orchestrator deploy examples/nginx.yaml
wait_for "3 pods RUNNING" 240 running_is nginx 3
orchestrator pods list --deployment nginx
nodes_used=$(orchestrator pods list --deployment nginx | awk 'NR>1 && $3=="RUNNING"{print $2}' | sort -u | wc -l)
[[ "$nodes_used" -ge 2 ]] || die "pods were not spread across workers"
ok "replicas distributed across $nodes_used workers"

step "7-8. Health checks pass and endpoints are registered"
wait_for "3 service endpoints" 120 bash -c '[[ $(orchestrator services get nginx | grep -c ":[0-9]") -ge 3 ]]'
orchestrator services get nginx
orchestrator services resolve nginx

step "9. Scale 3 -> 5"
orchestrator scale nginx --replicas 5
wait_for "5 pods RUNNING" 240 running_is nginx 5

step "10. Kill a container: it must be restarted"
docker compose exec -T dind-1 sh -c 'docker kill $(docker ps -q --filter label=orchestrator.managed=true | head -1)'
wait_for "restart recorded" 120 bash -c 'orchestrator events --type POD_RESTARTED | grep -q POD_RESTARTED'
wait_for "5 pods RUNNING again" 240 running_is nginx 5

step "11-13. Stop worker-1 (agent AND its Docker daemon): workloads must move to worker-2"
docker compose stop worker-1 dind-1
wait_for "worker-1 OFFLINE" 180 bash -c "orchestrator nodes list | grep worker-1 | grep -q OFFLINE"
wait_for "5 pods RUNNING on worker-2" 240 bash -c \
  'out=$(orchestrator pods list --deployment nginx); [[ $(grep -c " RUNNING " <<<"$out") == 5 && $(grep -c worker-1 <<<"$out") == 0 ]]'
orchestrator pods list --deployment nginx
orchestrator events --limit 15

step "14. Prometheus metrics reflect the cluster"
metrics=$(curl -fsS "$ORCHESTRATOR_URL/metrics")
grep -q '^nodes_offline 1.0' <<<"$metrics" || die "nodes_offline != 1"
grep -q '^pods_running 5.0' <<<"$metrics" || die "pods_running != 5"
ok "nodes_offline=1 pods_running=5"

step "Recovery: bring worker-1 back"
docker compose start dind-1 worker-1
wait_for "2 nodes READY" 180 nodes_ready_is 2
ok "acceptance scenario complete"

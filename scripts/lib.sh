#!/usr/bin/env bash
# Shared helpers for the demo and chaos scripts. Requires: orchestrator CLI, docker compose, curl.
set -euo pipefail
export ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8000}"
export ORCHESTRATOR_API_KEY="${ORCHESTRATOR_API_KEY:-${ADMIN_API_KEY:?set ADMIN_API_KEY or ORCHESTRATOR_API_KEY}}"

step() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32mOK\033[0m  %s\n' "$*"; }
die()  { printf '\033[1;31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# wait_for <description> <timeout-seconds> <command...>
wait_for() {
  local desc=$1 timeout=$2; shift 2
  local deadline=$((SECONDS + timeout))
  until "$@" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || die "timed out after ${timeout}s waiting for: $desc"
    sleep 2
  done
  ok "$desc"
}

count_running() { orchestrator pods list --deployment "${1:-nginx}" | grep -c ' RUNNING ' || true; }
running_is()    { [[ "$(count_running "$1")" == "$2" ]]; }
nodes_ready_is(){ [[ "$(orchestrator nodes list | grep -c ' READY ')" == "$1" ]]; }

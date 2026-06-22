#!/usr/bin/env bash
# One-shot: bring up the Dockerized backend AND the native host agent, then exit.
# Idempotent. The agent is detached so it survives this terminal closing. For an
# auto-restarting agent that tracks your session, use aether-keepalive.sh (what KDE
# autostart runs). No systemd involved.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_aether-lib.sh"
log() { printf '\033[36m[aether-up]\033[0m %s\n' "$*"; }

log "Starting Docker backend (FastAPI + Postgres + Redis)…"
start_backend

if healthy; then
  log "Host agent already running on :$PORT, nothing to do."
  exit 0
fi

[ -n "$(read_env AETHER_HOST_AGENT_TOKEN)" ] || log "WARNING: no AETHER_HOST_AGENT_TOKEN in .env, using default token."
log "Starting host agent (logs: $LOG)…"
start_agent_detached

for _ in $(seq 1 10); do
  healthy && { log "Host agent is up on :$PORT (pid $(cat "$PIDFILE"))."; exit 0; }
  sleep 0.5
done
log "Host agent did not report healthy yet, check $LOG."
exit 1

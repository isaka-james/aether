#!/usr/bin/env bash
# Stop the whole Aether stack: the supervisor (so it can't resurrect the agent), then
# the host agent, then the Dockerized backend.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_aether-lib.sh"
log() { printf '\033[36m[aether-down]\033[0m %s\n' "$*"; }

log "Stopping keepalive supervisor…"
SUPPID="$AGENT_DIR/keepalive.pid"
if [ -f "$SUPPID" ] && kill -0 "$(cat "$SUPPID")" 2>/dev/null; then
  kill "$(cat "$SUPPID")" 2>/dev/null || true   # its TERM trap stops the agent too
  sleep 1
else
  pkill -f "scripts/aether-keepalive.sh" 2>/dev/null || true
fi
rm -f "$AGENT_DIR/keepalive.lock" "$SUPPID"

log "Stopping host agent…"
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  stop_agent
else
  pkill -f "python3 agent.py" 2>/dev/null || true
  rm -f "$PIDFILE"
fi

log "Stopping Docker backend (backend + Postgres + Redis; data volumes kept)…"
docker compose -f "$ROOT/docker-compose.yml" down

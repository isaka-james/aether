#!/usr/bin/env bash
# Host-side supervisor, keeps the agent alive for the whole desktop session.
#
# Launched by KDE autostart. Brings the Docker backend up once, then watches the agent
# and restarts it within ~INTERVAL seconds of any crash or hang. The agent runs as a
# child of this loop, so it shares this session's audio/DBUS/Wayland env and dies on
# logout, the next login starts a clean supervisor + agent. Single-instance via flock.
# No systemd, no root, no SSH.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_aether-lib.sh"

INTERVAL="${AETHER_KEEPALIVE_INTERVAL:-5}"
SUPLOG="$AGENT_DIR/keepalive.log"
SUPPID="$AGENT_DIR/keepalive.pid"
log() { printf '%s [keepalive] %s\n' "$(date '+%F %T')" "$*" >>"$SUPLOG"; }

# Single instance: hold an exclusive lock for the life of the loop.
exec 9>"$AGENT_DIR/keepalive.lock"
flock -n 9 || { log "another supervisor already holds the lock, exiting."; exit 0; }
echo $$ >"$SUPPID"

cleanup() { log "shutting down, stopping agent"; stop_agent; rm -f "$SUPPID"; exit 0; }
trap cleanup TERM INT HUP

log "supervisor starting (interval ${INTERVAL}s, port $PORT)"
start_backend >>"$SUPLOG" 2>&1 || log "WARNING: 'docker compose up -d' failed; will supervise agent anyway"

while true; do
  if ! healthy; then
    stop_agent                       # clear any hung/stale instance still holding the port
    log "agent offline, (re)starting"
    start_agent_child
    sleep 2
    if healthy; then log "agent back up (pid $(cat "$PIDFILE" 2>/dev/null))"
    else log "agent not healthy yet, see $LOG"; fi
  fi
  sleep "$INTERVAL"
done

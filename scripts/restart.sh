#!/usr/bin/env bash
# Restart / refresh Aether — apply your latest changes without a full update.
#
#   bash scripts/restart.sh             # rebuild the backend (picks up backend code) + restart the agent
#   bash scripts/restart.sh --no-build  # just restart the running containers (faster; no rebuild)
#
# Backend Python is baked into the image, so a code change needs a rebuild — that's the default
# (Docker's layer cache makes it a ~1s no-op when nothing changed). The web client is live-mounted,
# so it never needs a rebuild: just refresh the browser. This is update.sh's restart half, without
# the git-pull / settings-merge steps.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/_aether-lib.sh"

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B= G= Y= R= N=; fi
say()  { printf '\n%s%s%s\n' "$B" "$*" "$N"; }
ok()   { printf '%s  ok%s  %s\n' "$G" "$N" "$*"; }
warn() { printf '%s  !%s   %s\n' "$Y" "$N" "$*"; }
err()  { printf '%s  x%s   %s\n' "$R" "$N" "$*" >&2; }

BUILD=1
case "${1:-}" in
  --no-build) BUILD=0 ;;
  "") ;;
  -h|--help) echo "Usage: bash scripts/restart.sh [--no-build]"; exit 0 ;;
  *) err "Unknown option: $1"; echo "Usage: bash scripts/restart.sh [--no-build]"; exit 2 ;;
esac

# --- 1. Backend (Dockerized: FastAPI + Postgres + Redis) --------------------
say "Restarting the Docker backend"
if ! docker info >/dev/null 2>&1; then
  err "Docker is not running. Start it (sudo systemctl start docker) and try again."
  exit 1
fi
if [ "$BUILD" = 1 ]; then
  start_backend && ok "Backend rebuilt and running (code changes applied)." \
    || warn "Backend rebuild reported a problem — check the output above."
else
  compose restart backend && ok "Backend container restarted (no rebuild)." \
    || warn "Could not restart the backend container — check the output above."
fi

# --- 2. Host agent (native; runs in your desktop session) -------------------
# Restart whichever way it's supervised, without racing the watcher into a double launch.
say "Restarting the host agent"
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active aether-agent >/dev/null 2>&1; then
  systemctl --user restart aether-agent && ok "Host agent restarted (systemd)."
elif [ -f "$AGENT_DIR/keepalive.pid" ] && kill -0 "$(cat "$AGENT_DIR/keepalive.pid" 2>/dev/null)" 2>/dev/null; then
  stop_agent; ok "Host agent stopped — the keepalive watcher brings it back with the new code in a few seconds."
else
  stop_agent; start_agent_detached; ok "Host agent restarted (logs: $LOG)."
fi

# --- 3. Wait for health, then point the user at the app ---------------------
for _ in $(seq 1 30); do healthy && break; sleep 1; done
web="$(read_env AETHER_WEB_PORT)"; web="${web:-8473}"
if healthy; then
  printf '\n%s  Aether is back up.%s Open http://localhost:%s — hard-refresh (Ctrl/Cmd+Shift+R) to clear the PWA cache.\n\n' "$G" "$N" "$web"
else
  warn "Host agent is still starting — give it a moment, then check $LOG"
fi

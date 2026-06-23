#!/usr/bin/env bash
# Shared helpers for the Aether launcher/supervisor scripts. Sourced, not run.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
AGENT_DIR="$ROOT/host-agent"
LOG="$AGENT_DIR/agent.log"
PIDFILE="$AGENT_DIR/agent.pid"

read_env() { [ -f "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//' || true; }
PORT="$(read_env AETHER_AGENT_PORT)"; PORT="${PORT:-8474}"

healthy()       { curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; }
# All compose commands target the project's compose file, so they work regardless of the
# caller's cwd (e.g. KDE autostart from $HOME); compose still reads $ROOT/.env from there.
# (Named in full — not 'dc' — because 'dc' is the Unix desk-calculator binary, which silently
# wins and hangs reading stdin in any context that re-execs the name instead of the function.)
compose() { docker compose -f "$ROOT/docker-compose.yml" "$@"; }

# Brings up the whole backend stack: FastAPI backend + Postgres (db) + Redis (redis).
#
# --build is deliberate. The image bakes in app/ and the Python deps (only web/ is live-
# mounted), so a code or requirements change is invisible to the running container until the
# image is rebuilt — that is exactly how a stale image once ran without the openai SDK. Docker's
# layer cache makes this a ~1s no-op when nothing changed, and compose only recreates the
# container when the image truly changes. start_backend runs once per login (keepalive) or per
# invocation (aether-up/update), never in a hot loop, so the cost is paid at most once per start.
start_backend() {
  compose up -d --build || return $?
  sync_db_password || true
}

# True when the backend reports a live Postgres connection (its /api/health is unauthenticated).
backend_db_ok() {
  local web; web="$(read_env AETHER_WEB_PORT)"; web="${web:-8473}"
  curl -fsS -m2 "http://127.0.0.1:$web/api/health" 2>/dev/null | grep -q '"database":true'
}

# Keep the Postgres role password in lockstep with .env. The postgres image only applies
# POSTGRES_PASSWORD when it first initialises the data volume; if the value in .env later
# changes (or the volume predates it), the backend can no longer authenticate and history /
# favourites silently switch off. Treat .env as the source of truth: if the backend cannot reach
# the database, converge the role password over the container's local socket (trust auth, no
# password needed) and restart the backend so its pool picks up working credentials. A quick
# no-op when the backend is already connected — drift detection is the backend's own health, not
# a hand-rolled probe (an in-container TCP probe proved able to hang indefinitely).
sync_db_password() {
  local pw i
  pw="$(read_env POSTGRES_PASSWORD)"; [ -n "$pw" ] || return 0
  [ -n "$(compose ps -q db 2>/dev/null)" ] || return 0      # db not up — nothing to reconcile
  for i in $(seq 1 20); do
    compose exec -T db pg_isready -U aether -d aether >/dev/null 2>&1 && break
    sleep 0.5
  done
  # Give the freshly (re)started backend up to ~30s to connect; if it can, we are in sync.
  for i in $(seq 1 30); do backend_db_ok && return 0; sleep 1; done
  # Still no database: the volume's password has drifted from .env. Converge it, then reconnect.
  printf "ALTER USER aether WITH PASSWORD :'pw';\n" \
    | compose exec -T db psql -U aether -d aether -v pw="$pw" -q >/dev/null 2>&1 \
    && compose restart backend >/dev/null 2>&1
}

stop_agent() {
  if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
}

# Detached: survives the launching terminal (nohup ignores SIGHUP). For the manual
# one-shot (aether-up.sh). exec chains so $! is the real python PID (no setsid fork).
start_agent_detached() {
  local t; t="$(read_env AETHER_HOST_AGENT_TOKEN)"
  ( cd "$AGENT_DIR" && exec nohup env AETHER_HOST_AGENT_TOKEN="$t" AETHER_AGENT_PORT="$PORT" \
      python3 agent.py >>"$LOG" 2>&1 ) &
  echo $! >"$PIDFILE"
}

# Child: tied to the caller's lifetime (dies on logout). For the supervisor loop, so a
# fresh agent is started at the next login with a valid audio/DBUS session.
start_agent_child() {
  local t; t="$(read_env AETHER_HOST_AGENT_TOKEN)"
  ( cd "$AGENT_DIR" && exec env AETHER_HOST_AGENT_TOKEN="$t" AETHER_AGENT_PORT="$PORT" \
      python3 agent.py >>"$LOG" 2>&1 ) &
  echo $! >"$PIDFILE"
}

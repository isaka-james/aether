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
# Brings up the whole backend stack: FastAPI backend + Postgres (db) + Redis (redis).
# Compose reads $ROOT/.env (incl. POSTGRES_PASSWORD) since the project dir is the compose
# file's dir, so this works regardless of the caller's cwd (e.g. KDE autostart from $HOME).
start_backend() { docker compose -f "$ROOT/docker-compose.yml" up -d; }

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

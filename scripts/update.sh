#!/usr/bin/env bash
# Update Aether to the latest version.
# Keeps your settings (.env) and your data, pulls the new code, rebuilds the backend,
# and restarts the assistant. Run it from the project folder anytime:
#
#   bash scripts/update.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/_aether-lib.sh"

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B= G= Y= R= N=; fi
say()  { printf '\n%s%s%s\n' "$B" "$*" "$N"; }
ok()   { printf '%s  ok%s  %s\n' "$G" "$N" "$*"; }
warn() { printf '%s  !%s   %s\n' "$Y" "$N" "$*"; }
err()  { printf '%s  x%s   %s\n' "$R" "$N" "$*" >&2; }

command -v git >/dev/null 2>&1 || { err "git is not installed."; exit 1; }
[ -d "$ROOT/.git" ] || { err "This folder is not a git checkout, so it cannot pull updates."; \
  echo "      Re-download with: git clone https://github.com/isaka-james/aether"; exit 1; }

# --- 1. Pull the latest code -----------------------------------------------
say "Step 1 of 4   Getting the latest version"
git -C "$ROOT" fetch --quiet 2>/dev/null || warn "Could not reach the server. Trying with what is here."
NEW="$(git -C "$ROOT" log --oneline --no-decorate HEAD..@{u} 2>/dev/null || true)"
if [ -z "$NEW" ]; then ok "Already up to date (or offline)."
else printf '  What is new:\n'; printf '%s\n' "$NEW" | head -10 | sed 's/^/    /'; fi
if ! git -C "$ROOT" pull --ff-only --quiet 2>/tmp/aether_pull.err; then
  err "Could not update automatically (you probably edited some tracked files)."
  echo "      Undo or stash your changes, then run this again. Details:"
  sed 's/^/      /' /tmp/aether_pull.err 2>/dev/null
  exit 1
fi
ok "Code updated."

# --- 2. Carry your settings forward ----------------------------------------
say "Step 2 of 4   Checking your settings"
added=0
if [ -f "$ENV_FILE" ] && [ -f "$ROOT/.env.example" ]; then
  tmp="$(mktemp)"
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue;; esac
    key="${line%%=*}"
    grep -qE "^${key}=" "$ENV_FILE" || { echo "$line" >> "$tmp"; added=$((added + 1)); }
  done < "$ROOT/.env.example"
  if [ "$added" -gt 0 ]; then
    { echo ""; echo "# ----- new settings added by update on $(date +%F), edit if you like -----"; cat "$tmp"; } >> "$ENV_FILE"
    warn "Added $added new setting(s) to your .env with their default values."
  else ok "Your settings are complete."; fi
  rm -f "$tmp"
fi

# --- 3. Rebuild the backend ------------------------------------------------
say "Step 3 of 4   Rebuilding the backend (your data and downloads are kept)"
if ! docker info >/dev/null 2>&1; then
  err "Docker is not running. Start it (sudo systemctl start docker) and run this again."
  exit 1
fi
start_backend && ok "Backend updated and running."

# --- 4. Restart the host agent with the new code ---------------------------
say "Step 4 of 4   Restarting the assistant"
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active aether-agent >/dev/null 2>&1; then
  systemctl --user restart aether-agent && ok "Host agent restarted."
elif [ -f "$AGENT_DIR/keepalive.pid" ] && kill -0 "$(cat "$AGENT_DIR/keepalive.pid" 2>/dev/null)" 2>/dev/null; then
  stop_agent; ok "Host agent stopped. The watcher brings it back with the new code in a few seconds."
else
  stop_agent; start_agent_detached; ok "Host agent restarted."
fi

for _ in $(seq 1 30); do healthy && break; sleep 1; done
if healthy; then printf '\n%s  Update complete. Refresh the page in your browser.%s\n\n' "$G" "$N"
else warn "The agent is still starting. Give it a minute, then check host-agent/agent.log"; fi

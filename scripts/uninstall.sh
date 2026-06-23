#!/usr/bin/env bash
# Remove Aether's autostart entry and stop the running stack. Leaves your .env,
# Docker images, and data volumes untouched (delete those yourself if you want).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTOSTART_FILE="$HOME/.config/autostart/aether.desktop"
log() { printf '\033[36m[aether-uninstall]\033[0m %s\n' "$*"; }

if [ -f "$AUTOSTART_FILE" ]; then rm -f "$AUTOSTART_FILE"; log "Removed autostart entry."; else log "No autostart entry found."; fi

log "Stopping the stack (supervisor, host agent, Docker backend)…"
bash "$ROOT/scripts/aether-down.sh" || true

# Also remove the optional systemd --user unit if it was installed that way.
if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files aether-agent.service >/dev/null 2>&1; then
  systemctl --user disable --now aether-agent.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/aether-agent.service"
  systemctl --user daemon-reload 2>/dev/null || true
  log "Removed systemd --user unit."
fi

log "Done. Aether will no longer start on login."
log "Kept: .env, Docker images, and data volumes. To remove data too:"
log "  docker compose -f \"$ROOT/docker-compose.yml\" down -v"

#!/usr/bin/env bash
# One-time setup for Aether's YouTube playback (Playwright as a CDP client only).
#
# We drive your *real* system Google Chrome over the DevTools protocol (CDP); Playwright
# is used purely as the CDP client (connect_over_cdp), so there is NO bundled-browser
# download — no `playwright install`. Playwright lives in a project-local venv at
# host-agent/.venv so the stdlib-only agent core stays untouched. Re-runnable.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/host-agent/.venv"
log() { printf '\033[36m[setup-browser]\033[0m %s\n' "$*"; }

log "Creating venv at $VENV…"
python3 -m venv "$VENV"

log "Installing Playwright (CDP client) into the venv…"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$ROOT/host-agent/requirements-browser.txt"

if ! command -v google-chrome >/dev/null 2>&1 && [ ! -x "${AETHER_CHROME_BIN:-/usr/bin/google-chrome}" ]; then
  log "NOTE: Google Chrome was not found. Install it (or set AETHER_CHROME_BIN), e.g.:"
  log "      sudo apt install ./google-chrome-stable_current_amd64.deb"
fi

log "Done — YouTube playback is ready. Try: 'play <song> on youtube'."

#!/usr/bin/env bash
# Set up ydotoold so Aether can simulate keyboard input on Wayland WITHOUT the KDE
# "an app requests remote control: input devices" portal popup.
#
# Why: xdotool drives input through XWayland's XTEST, which KDE Wayland gates behind that
# portal prompt — an interruption mid-action. ydotool injects events straight into the
# kernel via /dev/uinput (through the ydotoold daemon), which the compositor treats as a
# real device, so there is NO prompt. The host agent's skills/_input.py prefers ydotool
# whenever this daemon's socket is reachable.
#
# This installs ydotoold as a root system service with a world-connectable socket at
# /run/ydotoold/socket. Run once:  sudo bash scripts/setup-input.sh
set -euo pipefail
log() { printf '\033[36m[setup-input]\033[0m %s\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "This needs root (it installs a systemd service). Re-run: sudo bash $0" >&2
  exit 1
fi

if ! command -v ydotoold >/dev/null 2>&1; then
  log "Installing ydotool (provides ydotoold)…"
  apt-get update -y && apt-get install -y ydotool
fi

YDOTOOLD="$(command -v ydotoold)"
SOCKET=/run/ydotoold/socket

log "Writing /etc/systemd/system/ydotoold.service…"
cat > /etc/systemd/system/ydotoold.service <<EOF
[Unit]
Description=ydotool daemon (uinput input injection for Aether — no portal prompt)
Documentation=man:ydotoold(8)
After=systemd-user-sessions.service

[Service]
Type=simple
# socket-perm 0666 so the host agent (running as your user) can connect to this root daemon.
ExecStartPre=/usr/bin/install -d -m 0755 /run/ydotoold
ExecStart=${YDOTOOLD} --socket-path=${SOCKET} --socket-perm=0666
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

log "Enabling and starting the service…"
systemctl daemon-reload
systemctl enable --now ydotoold.service

sleep 1
if [ -S "$SOCKET" ]; then
  log "Done. Socket is live at $SOCKET — input simulation now bypasses the portal prompt."
else
  echo "[setup-input] WARNING: $SOCKET not present yet; check: systemctl status ydotoold" >&2
  exit 1
fi

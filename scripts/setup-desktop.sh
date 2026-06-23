#!/usr/bin/env bash
# Install the desktop tools Aether uses to control your machine. It works out which desktop
# you run (KDE, GNOME, XFCE, and others) and which package manager you have, then installs
# what is needed. Run it directly, or let the installer offer it:
#
#   sudo bash scripts/setup-desktop.sh
set -uo pipefail
log() { printf '\033[36m[setup-desktop]\033[0m %s\n' "$*"; }

# Work out the desktop before we escalate (sudo clears the desktop variables).
DE="${AETHER_DESKTOP:-$(printf '%s' "${XDG_CURRENT_DESKTOP:-}" | tr '[:upper:]' '[:lower:]')}"

# Re-run with sudo if needed, carrying the detected desktop along.
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || { echo "Please run as root: sudo bash $0"; exit 1; }
  exec sudo AETHER_DESKTOP="$DE" bash "$0" "$@"
fi

# Pick the package manager.
if   command -v apt-get >/dev/null 2>&1; then PM=apt;    INSTALL="apt-get install -y"
elif command -v dnf     >/dev/null 2>&1; then PM=dnf;    INSTALL="dnf install -y"
elif command -v pacman  >/dev/null 2>&1; then PM=pacman; INSTALL="pacman -S --noconfirm --needed"
elif command -v zypper  >/dev/null 2>&1; then PM=zypper; INSTALL="zypper install -y"
else
  log "I do not recognise your package manager. Install these by hand: pactl, notify-send,"
  log "nmcli, bluetoothctl, playerctl, brightnessctl, wmctrl, xdotool, xinput, and a screenshot tool."
  exit 1
fi
log "Package manager: $PM. Desktop: ${DE:-unknown}."
[ "$PM" = apt ] && apt-get update -y >/dev/null 2>&1 || true

# Tools that work on every desktop (names differ a little by distro). xdg-utils gives the
# generic screen lock and open-link; libgtk/gtk3 gives gtk-launch for opening apps by name;
# fswebcam gives the webcam photo; wl-clipboard/xclip give clipboard read and copy.
case "$PM" in
  apt)    UNIVERSAL="pulseaudio-utils libnotify-bin network-manager bluez playerctl brightnessctl wmctrl xdotool xinput power-profiles-daemon scrot xdg-utils libgtk-3-bin fswebcam wl-clipboard xclip";;
  dnf)    UNIVERSAL="pulseaudio-utils libnotify NetworkManager bluez playerctl brightnessctl wmctrl xdotool xinput power-profiles-daemon scrot xdg-utils gtk3 fswebcam wl-clipboard xclip";;
  pacman) UNIVERSAL="libpulse libnotify networkmanager bluez-utils playerctl brightnessctl wmctrl xdotool xorg-xinput power-profiles-daemon scrot xdg-utils gtk3 fswebcam wl-clipboard xclip";;
  zypper) UNIVERSAL="pulseaudio-utils libnotify-tools NetworkManager bluez playerctl brightnessctl wmctrl xdotool xinput power-profiles-daemon scrot xdg-utils gtk3-tools fswebcam wl-clipboard xclip";;
esac

# Desktop-specific extras (mostly the screenshot tool; qdbus for KDE's own controls).
case "$DE" in
  *kde*|*plasma*) case "$PM" in
                    apt)    EXTRA="kde-spectacle qttools5-dev-tools";;
                    dnf)    EXTRA="spectacle qt5-qttools";;
                    *)      EXTRA="spectacle";; esac;;
  *gnome*)        EXTRA="gnome-screenshot";;
  *xfce*)         case "$PM" in dnf) EXTRA="xfce4-screenshooter-plugin";; *) EXTRA="xfce4-screenshooter";; esac;;
  *sway*|*hypr*|*wlroots*|*river*) EXTRA="grim";;
  *)              EXTRA="gnome-screenshot grim";;   # unknown desktop: add common screenshot tools
esac

log "Installing. A name that does not exist on your distro is skipped, which is normal."
missed=""
for p in $UNIVERSAL $EXTRA; do
  if $INSTALL "$p" >/dev/null 2>&1; then printf '  ok    %s\n' "$p"
  else printf '  skip  %s\n' "$p"; missed="$missed $p"; fi
done

log "Done. Aether can now handle audio, wifi, bluetooth, brightness, windows, and screenshots here."
[ -n "$missed" ] && log "If a feature is missing later, its tool may be one of these (install it by hand):$missed"
exit 0

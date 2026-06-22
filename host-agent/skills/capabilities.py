"""Capability probe and tool discovery.

``capabilities`` reports which actions actually work on this machine, by checking which
tools are installed. The backend folds the result into the model's prompt so the agent
knows what it can do here, and the agent can also call it directly. ``find_tool`` lets the
agent search the installed programs for something it needs before using it.
"""
from __future__ import annotations

import os
import platform
import socket

from ._util import QDBUS, fail, has, ok
from .registry import skill


def _distro() -> str:
    """The friendly OS name from /etc/os-release, e.g. 'Ubuntu 24.04.1 LTS'."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system() or "Linux"


def machine() -> dict:
    """Static facts about this computer the agent should know: desktop, session, distro, host."""
    return {
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "",
        "session_type": os.environ.get("XDG_SESSION_TYPE") or "",   # wayland | x11 | tty
        "distro": _distro(),
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "arch": platform.machine(),
    }


def _any(*tools: str) -> bool:
    return any(has(t) for t in tools)


def _has_backlight() -> bool:
    try:
        return any(True for _ in os.scandir("/sys/class/backlight"))
    except OSError:
        return False


def _has_webcam() -> bool:
    try:
        return any(n.startswith("video") for n in os.listdir("/dev"))
    except OSError:
        return False


def probe() -> dict:
    """A flat map of capability -> bool for what works on this machine right now."""
    return {
        "audio": has("pactl"),
        "microphone": has("pactl"),
        "bluetooth": has("bluetoothctl"),
        "wifi": has("nmcli"),
        "media_control": _any("playerctl", QDBUS),
        "brightness": has("brightnessctl") or _has_backlight() or has(QDBUS),
        "screenshot": _any("spectacle", "gnome-screenshot", "xfce4-screenshooter", "grim",
                           "scrot", "maim", "import"),
        "windows": _any("wmctrl", QDBUS),
        "keyboard_input": _any("ydotool", "xdotool"),
        "input_devices": has("xinput"),
        "notifications": has("dbus-monitor"),
        "lock_screen": _any("loginctl", "dbus-send", "xdg-screensaver", QDBUS),
        "power_actions": has("systemctl"),
        "power_profile": has("powerprofilesctl"),
        "youtube": _any("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"),
        "local_music": _any("vlc", "mpv", "ffplay"),
        "camera": _has_webcam() and _any("fswebcam", "ffmpeg"),
        "clipboard": _any("wl-paste", "wl-copy", "xclip", "xsel"),
        "open_url": has("xdg-open"),
        "weather": True,
    }


@skill("capabilities")
def capabilities(_):
    """Report this machine (desktop, session, distro) and what works (which tools are installed)."""
    caps = probe()
    m = machine()
    working = sorted(k for k, v in caps.items() if v)
    missing = sorted(k for k, v in caps.items() if not v)
    summary = (f"{m.get('distro')} on the {m.get('desktop') or 'unknown'} desktop "
               f"({m.get('session_type') or 'unknown'} session). {len(working)} capabilities available.")
    if missing:
        summary += " Unavailable (tool not installed): " + ", ".join(missing) + "."
    return ok(summary, capabilities=caps, machine=m, available=working, unavailable=missing)


@skill("find_tool")
def find_tool(params):
    """Search the installed programs for ones whose name contains `query`. Lets the agent
    discover a tool before using it (e.g. find_tool 'pdf' to see what can handle PDFs)."""
    q = str(params.get("query", "")).strip().lower()
    if not q:
        return fail("What kind of tool are you looking for?")
    seen: set[str] = set()
    matches: list[str] = []
    for d in (os.environ.get("PATH") or "/usr/bin:/bin").split(os.pathsep):
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for name in entries:
            if q in name.lower() and name not in seen:
                p = os.path.join(d, name)
                if not os.path.isdir(p) and os.access(p, os.X_OK):
                    seen.add(name)
                    matches.append(name)
    matches.sort()
    if not matches:
        return ok(f"No installed program matches '{q}'.", matches=[], query=q)
    head = ", ".join(matches[:30]) + ("…" if len(matches) > 30 else "")
    return ok(f"{len(matches)} installed program(s) matching '{q}': {head}",
              matches=matches[:60], query=q)

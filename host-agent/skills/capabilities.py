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

from ._util import QDBUS, fail, has, ok, run
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
        "ocr": has("tesseract"),
        "do_not_disturb": _any("kwriteconfig6", "gsettings"),
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


def _path_executables() -> "set[str]":
    """Every executable name on PATH (used to keep discovery to things actually installed)."""
    names: set[str] = set()
    for d in (os.environ.get("PATH") or "/usr/bin:/bin").split(os.pathsep):
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for name in entries:
            p = os.path.join(d, name)
            try:
                if not os.path.isdir(p) and os.access(p, os.X_OK):
                    names.add(name)
            except OSError:
                continue
    return names


def _by_name(q: str, execs: "set[str]") -> list[str]:
    """Installed executables whose filename contains the query."""
    return sorted(n for n in execs if q in n.lower())


def _by_purpose(q: str, execs: "set[str]") -> list[str]:
    """Installed programs whose man-page description mentions the query (so 'find a tool that does
    X' works even when the program isn't named after X). Empty if apropos/man-db isn't present."""
    if not has("apropos"):
        return []
    rc, out, _ = run(["apropos", "--", q], timeout=8)
    if rc != 0 or not out:
        return []
    found: list[str] = []
    for line in out.splitlines():
        # "name (section) - description" — possibly "name1, name2 (1) - ..."
        head = line.split(" - ", 1)[0]
        for token in head.split("(", 1)[0].split(","):
            name = token.strip()
            if name and name in execs and name not in found:
                found.append(name)
    return found


def _describe(names: list[str]) -> dict[str, str]:
    """One-line descriptions for `names` from `whatis` (man-page summaries). Best-effort: missing
    man-db just yields no descriptions, and the names are still returned."""
    if not names or not has("whatis"):
        return {}
    rc, out, _ = run(["whatis", "--"] + names, timeout=8)
    if rc != 0 or not out:
        return {}
    desc: dict[str, str] = {}
    for line in out.splitlines():
        if " - " not in line:
            continue
        head, summary = line.split(" - ", 1)
        for token in head.split("(", 1)[0].split(","):
            name = token.strip()
            if name and name not in desc:
                desc[name] = summary.strip()
    return desc


@skill("find_tool")
def find_tool(params):
    """Discover an installed program for a need — by name AND by what it does. Searches PATH for
    executables whose name contains `query`, plus (via apropos) programs whose man-page description
    mentions it, then annotates each with a one-line summary (via whatis). So find_tool 'pdf' turns
    up not just 'pdfunite' but things like 'qpdf — PDF transformation software', and the agent can
    pick the right one and know how to use it before running it."""
    q = str(params.get("query", "")).strip().lower()
    if not q:
        return fail("What kind of tool are you looking for?")
    execs = _path_executables()
    ordered: list[str] = []
    for name in _by_name(q, execs) + _by_purpose(q, execs):   # name matches first, then purpose
        if name not in ordered:
            ordered.append(name)
    if not ordered:
        return ok(f"No installed program matches '{q}' by name or description.", matches=[],
                  tools=[], query=q)
    descs = _describe(ordered[:25])
    tools = [{"name": n, "description": descs.get(n, "")} for n in ordered[:60]]
    head = "; ".join(f"{t['name']} — {t['description']}" if t["description"] else t["name"]
                     for t in tools[:12]) + ("…" if len(ordered) > 12 else "")
    return ok(f"{len(ordered)} program(s) matching '{q}': {head}",
              matches=ordered[:60], tools=tools, query=q)

"""KWin scripting helper — the reliable way to inspect/act on windows under Wayland.

We load a small JS script into KWin via DBus, run it, and read its ``print()`` output
back from the user journal (tagged with a per-call nonce). This sees native Wayland
windows that X11 tools (wmctrl/xdotool) cannot.
"""
from __future__ import annotations

import os
import secrets
import tempfile
import time

from ._util import QDBUS, has, run


# Friendly names for common window resource-classes.
FRIENDLY = {
    "alacritty": "terminal", "konsole": "terminal", "yakuake": "terminal", "kitty": "terminal",
    "org.kde.konsole": "terminal", "org.kde.yakuake": "terminal", "xterm": "terminal",
    "org.kde.dolphin": "Dolphin", "code": "VS Code", "code-oss": "VS Code", "codium": "VS Code",
    "firefox": "Firefox", "chromium": "Chromium", "google-chrome": "Chrome",
}


# Generic categories → known window resource-classes.
CATEGORIES = {
    "terminal": {"alacritty", "konsole", "org.kde.konsole", "yakuake", "org.kde.yakuake",
                 "kitty", "xterm", "gnome-terminal", "terminator"},
    "browser": {"firefox", "org.mozilla.firefox", "chromium", "google-chrome", "chrome", "brave-browser"},
    "editor": {"code", "code-oss", "codium", "kate", "org.kde.kate", "sublime_text", "gedit", "org.kde.kwrite"},
    "file manager": {"org.kde.dolphin", "dolphin", "nautilus", "nemo"},
}


def label(app: str) -> str:
    return FRIENDLY.get((app or "").lower(), app)


def available() -> bool:
    return has(QDBUS) or QDBUS == "qdbus6"


def run_script(body: str, nonce: str, settle: float = 0.5) -> list[str]:
    """Run a KWin script and return printed lines that contain ``nonce``."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, dir="/tmp") as f:
        f.write(body)
        path = f.name
    try:
        since = time.strftime("%Y-%m-%d %H:%M:%S")
        rc, sid, _ = run([QDBUS, "org.kde.KWin", "/Scripting",
                          "org.kde.kwin.Scripting.loadScript", path])
        if rc != 0 or not sid.strip().lstrip("-").isdigit():
            return []
        sid = sid.strip()
        run([QDBUS, "org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script.run"])
        run([QDBUS, "org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script.stop"])
        time.sleep(settle)
        _, out, _ = run(["journalctl", "--user", "-b", "--since", since, "--no-pager"], timeout=8)
        lines = []
        for line in out.splitlines():
            i = line.find(nonce)
            if i != -1:
                lines.append(line[i:])
        return lines
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_ITER = ('var ws=(typeof workspace.windowList==="function")?workspace.windowList():'
         '(typeof workspace.clientList==="function")?workspace.clientList():[];')


def list_windows() -> list[dict]:
    """Return [{app, title}] for normal, taskbar-visible windows."""
    nonce = "AW" + secrets.token_hex(4)
    body = (_ITER + 'ws.forEach(function(w){if(w&&w.normalWindow&&!w.skipTaskbar){'
            f'print("{nonce}|"+(w.resourceClass||"?")+"|"+(w.caption||""));}}}});')
    wins = []
    for line in run_script(body, nonce):
        parts = line.split("|", 2)
        if len(parts) == 3:
            wins.append({"app": parts[1], "title": parts[2].strip()})
    return wins


def act_on_window(match: str, action: str) -> int:
    """close or focus windows whose caption/class contains `match` (case-insensitive).
    Returns the number of matched windows."""
    nonce = "AA" + secrets.token_hex(4)
    m = match.lower().replace('"', '')
    verb = ("w.closeWindow();" if action == "close"
            else "workspace.activeWindow=w;" if action == "focus" else "")
    body = (_ITER + f'var t="{m}";var n=0;ws.forEach(function(w){{if(w&&w.normalWindow){{'
            'var c=((w.caption||"")+" "+(w.resourceClass||"")).toLowerCase();'
            f'if(c.indexOf(t)>=0){{n++;{verb}}}}}}});print("{nonce}|"+n);')
    for line in run_script(body, nonce):
        parts = line.split("|", 1)
        if len(parts) == 2 and parts[1].strip().isdigit():
            return int(parts[1].strip())
    return 0

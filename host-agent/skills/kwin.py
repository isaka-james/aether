"""Window inspection and control, portable across desktops.

On KDE it loads a small JS script into KWin over DBus and reads its ``print()`` output back
from the user journal (tagged with a per-call nonce); this sees native Wayland windows that
X11 tools cannot. On other desktops it falls back to wmctrl over X11 / XWayland, so listing,
closing, and focusing windows work on GNOME, XFCE, MATE, Cinnamon, and the rest.
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


def _kwin_list() -> list[dict]:
    """KWin-scripting listing (KDE; sees native Wayland windows too)."""
    nonce = "AW" + secrets.token_hex(4)
    body = (_ITER + 'ws.forEach(function(w){if(w&&w.normalWindow&&!w.skipTaskbar){'
            f'print("{nonce}|"+(w.resourceClass||"?")+"|"+(w.caption||""));}}}});')
    wins = []
    for line in run_script(body, nonce):
        parts = line.split("|", 2)
        if len(parts) == 3:
            wins.append({"app": parts[1], "title": parts[2].strip()})
    return wins


def _kwin_act(match: str, action: str) -> int:
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


def _wmctrl_list() -> list[dict]:
    """Portable X11 / XWayland listing via wmctrl. Works on GNOME, XFCE, MATE, Cinnamon, etc."""
    if not has("wmctrl"):
        return []
    rc, out, _ = run(["wmctrl", "-lx"])
    if rc != 0:
        return []
    wins = []
    for line in out.splitlines():
        parts = line.split(None, 4)          # id, desktop, WM_CLASS, host, title
        if len(parts) < 4:
            continue
        app = parts[2].split(".")[0].lower()  # WM_CLASS instance, matches resourceClass
        title = parts[4].strip() if len(parts) == 5 else ""
        wins.append({"app": app, "title": title, "_id": parts[0]})
    return wins


def _wmctrl_act(match: str, action: str) -> int:
    flag = "-ic" if action == "close" else "-ia" if action == "focus" else None
    if flag is None:
        return 0
    t = match.lower()
    n = 0
    for w in _wmctrl_list():
        if t in (w["title"] + " " + w["app"]).lower():
            run(["wmctrl", flag, w["_id"]])
            n += 1
    return n


def list_windows() -> list[dict]:
    """Open windows as [{app, title}]. Uses KWin on KDE (incl. native Wayland), wmctrl elsewhere."""
    return _kwin_list() or _wmctrl_list()


def act_on_window(match: str, action: str) -> int:
    """Close or focus windows matching `match`. Tries KWin, then wmctrl. Returns the count acted on."""
    return _kwin_act(match, action) or _wmctrl_act(match, action)

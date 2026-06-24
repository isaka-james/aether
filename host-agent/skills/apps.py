"""Application skills: launch, close, and list running graphical apps; open links and files."""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess

from . import kwin
from ._util import fail, has, ok, run
from .registry import skill


@skill("open_url")
def open_url(params):
    """Open a website, link, file, or folder in its default app (via xdg-open)."""
    target = str(params.get("url") or params.get("target") or "").strip()
    if not target:
        return fail("What should I open? Give a web address, a file, or a folder.")
    # A bare domain like 'github.com' becomes a web address; '~/x' expands; real paths stay.
    if (not target.startswith(("http://", "https://", "file://", "mailto:", "/", "~", "."))
            and "." in target and " " not in target):
        target = "https://" + target
    target = os.path.expanduser(target)
    if not has("xdg-open"):
        return fail("xdg-open isn't installed (it comes with xdg-utils).")
    subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return ok(f"Opening {target}.", target=target)


def _launch(app_id: str) -> str | None:
    """Launch the way the desktop expects, trying the portable options in turn; return the
    method that worked, or None.
      gtk-launch -> GNOME / XFCE (and KDE when installed)
      kstart     -> KDE Plasma
      the binary on PATH -> works on any desktop"""
    if has("gtk-launch"):
        rc, _, _ = run(["gtk-launch", app_id], timeout=10)
        if rc == 0:
            return "gtk-launch"
    for k in ("kstart", "kstart6", "kstart5"):
        if has(k):
            rc, _, _ = run([k, app_id], timeout=10)
            if rc == 0:
                return k
    binary = shutil.which(app_id)
    if binary:
        subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return "exec"
    return None


def _desktop_app_ids() -> list[str]:
    """Installed desktop-application IDs (the .desktop basename) from the XDG data dirs — the
    set of GUI apps the user could 'open'. Used only to resolve a near-miss name to a real one."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    ids: set[str] = set()
    for base in [home, *data_dirs.split(":")]:
        try:
            for fn in os.listdir(os.path.join(base, "applications")):
                if fn.endswith(".desktop"):
                    ids.add(fn[:-8])
        except OSError:
            pass
    return sorted(ids)


def _best_app(app_id: str, candidates: list[str]) -> str | None:
    """Closest installed app to a slightly-off or nickname'd request — exact, then substring
    either way (so 'chrome' -> 'google-chrome'), then a difflib fuzzy match (so 'chrom' ->
    'chromium'). Returns a real installed id or None; never an arbitrary path."""
    low = (app_id or "").strip().lower()
    if not low or not candidates:
        return None
    by_low = {c.lower(): c for c in candidates}
    if low in by_low:
        return by_low[low]
    subs = [c for c in candidates if low in c.lower() or c.lower() in low]
    if subs:
        return min(subs, key=len)
    close = difflib.get_close_matches(low, list(by_low), n=1, cutoff=0.6)
    return by_low[close[0]] if close else None


@skill("open_app")
def open_app(params):
    app = str(params.get("app", "")).strip()
    if not app:
        return fail("No application name was given.")
    # Only launch by plain name, never a path, so a crafted value can't run an arbitrary binary.
    if "/" in app or "\\" in app or app.startswith("."):
        return fail("Please give a plain application name, not a path.")
    app_id = app[:-8] if app.endswith(".desktop") else app
    method = _launch(app_id)
    if method:
        return ok(f"Opening {app}.", method=method)
    # Fuzzy fallback: a slightly-off or nickname'd name ("chrom", or "chrome" for google-chrome).
    # Resolve it against installed desktop apps and retry — still only known, installed ids.
    resolved = _best_app(app_id, _desktop_app_ids())
    if resolved and resolved.lower() != app_id.lower():
        method = _launch(resolved)
        if method:
            return ok(f"Opening {resolved}.", method=method, matched_from=app)
    return fail(f"I couldn't find an application called {app}.")


@skill("close_app")
def close_app(params):
    app = str(params.get("app", "")).strip()
    if not app:
        return fail("Which application should I close?")
    rc, _, _ = run(["pkill", "-TERM", "-i", app])  # user's own processes; no root
    if rc == 0:
        return ok(f"Closed {app}.", app=app)
    return fail(f"{app} doesn't seem to be running.", app=app)


@skill("running_apps")
def running_apps(_):
    # GUI apps with a window (Wayland-reliable via KWin).
    apps = sorted({w["app"] for w in kwin.list_windows()})
    if not apps:
        return ok("No applications with open windows were found.", apps=[])
    return ok(f"{len(apps)} app(s) with open windows: " + ", ".join(apps) + ".",
              apps=apps, count=len(apps))


@skill("is_running")
def is_running(params):
    """Check whether an app/process is running. Matches both window classes and processes."""
    name = str(params.get("name", "")).strip().lower()
    if not name:
        return fail("Which application should I check for?")
    category = kwin.CATEGORIES.get(name)
    windows = []
    for w in kwin.list_windows():
        app = w["app"].lower()
        if category:
            if app in category:
                windows.append(w)
        elif name in app or name in kwin.label(w["app"]).lower():
            windows.append(w)
    if windows:
        apps = sorted({kwin.label(w["app"]) for w in windows})
        return ok(f"Yes — {name} is open: {len(windows)} window(s) ({', '.join(apps)}).",
                  running=True, windows=windows)
    rc, procs, _ = run(["pgrep", "-i", name])  # precise name match (no full-cmdline)
    if procs.strip():
        return ok(f"Yes — {name} is running (no visible window).", running=True)
    return ok(f"No — {name} doesn't appear to be running.", running=False)

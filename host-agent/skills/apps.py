"""Application skills: launch, close, and list running graphical apps; open links and files."""
from __future__ import annotations

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


@skill("open_app")
def open_app(params):
    app = str(params.get("app", "")).strip()
    if not app:
        return fail("No application name was given.")
    # Only launch by plain name, never a path, so a crafted value can't run an arbitrary binary.
    if "/" in app or "\\" in app or app.startswith("."):
        return fail("Please give a plain application name, not a path.")
    app_id = app[:-8] if app.endswith(".desktop") else app
    # Launch the way the desktop expects, trying the portable options in turn:
    #   gtk-launch  -> GNOME / XFCE (and KDE when installed)
    #   kstart      -> KDE Plasma
    #   the binary on PATH -> works on any desktop
    if has("gtk-launch"):
        rc, _, _ = run(["gtk-launch", app_id], timeout=10)
        if rc == 0:
            return ok(f"Opening {app}.", method="gtk-launch")
    for k in ("kstart", "kstart6", "kstart5"):
        if has(k):
            rc, _, _ = run([k, app_id], timeout=10)
            if rc == 0:
                return ok(f"Opening {app}.", method=k)
    binary = shutil.which(app_id)
    if binary:
        subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return ok(f"Opening {app}.", method="exec")
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

"""Application skills: launch, close, and list running graphical apps."""
from __future__ import annotations

import shutil
import subprocess

from . import kwin
from ._util import fail, ok, run
from .registry import skill


@skill("open_app")
def open_app(params):
    app = str(params.get("app", "")).strip()
    if not app:
        return fail("No application name was given.")
    desktop_id = app if app.endswith(".desktop") else f"{app}.desktop"
    rc, _, err = run(["gtk-launch", desktop_id], timeout=10)
    if rc == 0:
        return ok(f"Opening {app}.", method="gtk-launch")
    binary = shutil.which(app)
    if binary:
        subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return ok(f"Opening {app}.", method="exec")
    return fail(f"I couldn't find an application called {app}.", error=err)


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

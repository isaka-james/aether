"""System skills: stats, power profile, screenshot, lock, notifications."""
from __future__ import annotations

import os
import shutil
import time

import notify_recorder

from ._util import QDBUS, fail, has, ok, run
from .registry import skill


def _meminfo():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, v = line.partition(":")
            info[k] = int(v.strip().split()[0])  # kB
    return info


def _battery():
    base = "/sys/class/power_supply"
    if not os.path.isdir(base):
        return None
    for name in sorted(os.listdir(base)):
        if name.startswith("BAT"):
            try:
                cap = open(f"{base}/{name}/capacity").read().strip()
                status = open(f"{base}/{name}/status").read().strip()
                return f"{cap}% ({status.lower()})"
            except OSError:
                return None
    return None


@skill("system_info")
def system_info(params):
    what = str(params.get("what", "all")).lower()
    if what in ("system", "os", "desktop", "distro", "session", "machine"):
        from .capabilities import machine
        mi = machine()
        return ok(f"{mi.get('distro') or 'Linux'}, the {mi.get('desktop') or 'unknown'} desktop, "
                  f"{mi.get('session_type') or 'unknown'} session, host {mi.get('hostname')}.", **mi)
    parts, data = [], {}
    mem = _meminfo()
    total = mem["MemTotal"] / 1024 / 1024
    avail = mem.get("MemAvailable", 0) / 1024 / 1024

    if what in ("ram", "memory", "all"):
        parts.append(f"RAM: {total - avail:.1f} of {total:.1f} GB used, {avail:.1f} GB free")
        data["ram"] = {"total_gb": round(total, 1), "used_gb": round(total - avail, 1)}
    if what in ("cpu", "load", "all"):
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 1
        parts.append(f"CPU load: {load1:.2f} (1m) across {cores} cores")
        data["cpu"] = {"load1": round(load1, 2), "cores": cores}
    if what in ("disk", "all"):
        du = shutil.disk_usage("/")
        parts.append(f"Disk: {du.used / 1e9:.0f} of {du.total / 1e9:.0f} GB used")
        data["disk"] = {"total_gb": round(du.total / 1e9), "used_gb": round(du.used / 1e9)}
    if what in ("battery", "power", "all") and (bat := _battery()):
        parts.append(f"Battery: {bat}")
        data["battery"] = bat
    if what == "all":
        from .capabilities import machine
        mi = machine()
        parts.append(f"System: {mi.get('distro') or 'Linux'} on {mi.get('desktop') or 'unknown'} "
                     f"({mi.get('session_type') or 'unknown'})")
        data["system"] = mi

    return ok(". ".join(parts) + "." if parts else "No info available.", **data)


@skill("power_profile")
def power_profile(params):
    profile = str(params.get("profile", "")).strip()
    if profile:
        rc, _, err = run(["powerprofilesctl", "set", profile])
        return ok(f"Power profile set to {profile}.", profile=profile) if rc == 0 \
            else fail(f"Couldn't set profile '{profile}'.", error=err)
    rc, out, _ = run(["powerprofilesctl", "get"])
    return ok(f"Current power profile is {out.strip() or 'unknown'}.", profile=out.strip())


@skill("screenshot")
def screenshot(_):
    path = f"/tmp/aether-shot-{int(time.time())}.png"
    # Use whichever screenshot tool is installed and actually writes the file. Covers KDE
    # (spectacle), GNOME (gnome-screenshot), XFCE, wlroots/sway (grim), and plain X11.
    candidates = [
        ("spectacle", ["spectacle", "-f", "-b", "-n", "-o", path]),
        ("gnome-screenshot", ["gnome-screenshot", "-f", path]),
        ("xfce4-screenshooter", ["xfce4-screenshooter", "-f", "-s", path]),
        ("grim", ["grim", path]),
        ("scrot", ["scrot", "-o", path]),
        ("maim", ["maim", path]),
        ("import", ["import", "-window", "root", path]),  # ImageMagick
    ]
    last = ""
    for tool, argv in candidates:
        if not has(tool):
            continue
        _, _, err = run(argv, timeout=15)
        if os.path.exists(path):
            return ok("Screenshot captured.", path=path, tool=tool)
        last = err or last
    return fail("Couldn't take a screenshot. Install one of: spectacle, gnome-screenshot, "
                "grim, or scrot.", error=last)


@skill("lock_screen")
def lock_screen(_):
    # Try the portable ways in turn, so it works on KDE, GNOME, XFCE, and most others.
    attempts = [
        ["loginctl", "lock-session"],                                   # logind: honoured by most desktops
        ["dbus-send", "--session", "--type=method_call",
         "--dest=org.freedesktop.ScreenSaver",
         "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],  # freedesktop (GNOME/KDE)
        ["dbus-send", "--session", "--type=method_call",
         "--dest=org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver.Lock"],
        ["xdg-screensaver", "lock"],                                    # xdg-utils
        ["gnome-screensaver-command", "-l"],
        ["xfce4-screensaver-command", "-l"],
        [QDBUS, "org.freedesktop.ScreenSaver", "/ScreenSaver", "Lock"],  # KDE
    ]
    err = ""
    for argv in attempts:
        rc, _, err = run(argv)
        if rc == 0:
            return ok("Locking the screen.")
    return fail("Couldn't lock the screen.", error=err)


@skill("unlock_screen")
def unlock_screen(_):
    # Unlock our own graphical session via logind. This clears the lock without a password
    # (the request already came from an authenticated Aether session); on a hardened setup
    # polkit may still refuse, in which case we report that plainly.
    rc, _, err = run(["loginctl", "unlock-session"])
    if rc != 0:
        rc, _, err = run(["loginctl", "unlock-sessions"])
    if rc == 0:
        return ok("Unlocking the screen.")
    return fail("Couldn't unlock the screen — the session may require a password at the lock screen.",
                error=err)


# Power/session actions, mapped to logind (systemctl) and KDE. Reboot/poweroff/logout are
# disruptive but reversible; the LLM only reaches them on an explicit request.
_POWER = {
    "suspend":   (["systemctl", "suspend"], "Suspending."),
    "sleep":     (["systemctl", "suspend"], "Suspending."),
    "hibernate": (["systemctl", "hibernate"], "Hibernating."),
    "reboot":    (["systemctl", "reboot"], "Rebooting."),
    "restart":   (["systemctl", "reboot"], "Rebooting."),
    "poweroff":  (["systemctl", "poweroff"], "Shutting down."),
    "shutdown":  (["systemctl", "poweroff"], "Shutting down."),
}


@skill("power_action")
def power_action(params):
    action = str(params.get("action", "")).lower().strip()
    if action in ("logout", "log out", "sign out"):
        # Try each desktop's logout, then fall back to ending our own session via logind.
        err = ""
        for argv in ([QDBUS, "org.kde.Shutdown", "/Shutdown", "logout"],
                     ["gnome-session-quit", "--logout", "--no-prompt"],
                     ["xfce4-session-logout", "--logout"],
                     ["loginctl", "terminate-user", str(os.getuid())]):
            rc, _, err = run(argv)
            if rc == 0:
                return ok("Logging out.")
        return fail("Couldn't log out.", error=err)
    if action not in _POWER:
        return fail(f"Unknown power action '{action}'. Try suspend, hibernate, reboot, "
                    "shutdown, or logout.")
    argv, msg = _POWER[action]
    rc, _, err = run(argv)
    return ok(msg) if rc == 0 else fail(f"Couldn't {action} — it may need privileges.", error=err)


def _dnd_on() -> bool:
    _, out, _ = run(["kreadconfig6", "--file", "plasmanotifyrc",
                     "--group", "DoNotDisturb", "--key", "Until"])
    return bool(out.strip()) and out.strip() != "0"


@skill("notifications")
def notifications(params):
    """Recent desktop notifications captured live from the session bus by notify_recorder.

    Plasma exposes no API to read notification history, so we report what the recorder has
    seen since the agent started. `since` (epoch float) returns only newer ones — used by
    the backend poller to archive/fan-out; `limit` caps the count."""
    since = params.get("since")
    try:
        since = float(since) if since is not None else None
    except (TypeError, ValueError):
        since = None
    limit = max(1, min(100, int(params.get("limit", 10) or 10)))
    items = notify_recorder.read(since=since, limit=limit)
    dnd = _dnd_on()
    latest_ts = items[-1]["ts"] if items else (since or 0)
    data = {"items": items, "count": len(items), "do_not_disturb": dnd, "latest_ts": latest_ts}

    if not notify_recorder.available():
        return ok("I can't watch notifications here — dbus-monitor or the session bus is "
                  "unavailable.", **data)
    if not items:
        msg = "No notifications" + (" (Do Not Disturb is on)" if dnd else "") + " since I started watching."
        return ok(msg, **data)

    recent = list(reversed(items))[:5]  # newest first for the spoken summary
    heads = []
    for r in recent:
        who = (r.get("app") or "").strip()
        summ = (r.get("summary") or r.get("body") or "").strip()
        heads.append(f"{who}: {summ}" if who and summ else (summ or who or "a notification"))
    lead = f"{len(items)} notification" + ("s" if len(items) != 1 else "")
    dnd_note = " Do Not Disturb is on." if dnd else ""
    return ok(f"{lead} — " + "; ".join(heads) + "." + dnd_note, **data)


@skill("clear_notifications")
def clear_notifications(_):
    n = notify_recorder.clear()
    return ok(f"Cleared {n} notification" + ("s." if n != 1 else "."), cleared=n)


@skill("notify")
def notify(params):
    message = str(params.get("message", "")).strip() or "Notification from Aether."
    rc, _, err = run(["notify-send", "Aether", message])
    return ok("Notification sent.") if rc == 0 else fail("Couldn't send the notification.", error=err)

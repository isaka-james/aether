#!/usr/bin/env python3
"""Notification recorder: the host agent's eyes on desktop notifications.

Desktop notifications follow the freedesktop spec and are fire-and-forget: apps call the
D-Bus method ``org.freedesktop.Notifications.Notify(...)`` and whatever notification server
the desktop runs (Plasma, GNOME Shell, Xfce, Dunst, and so on) shows them. There is no API to
read past ones back, so the only way to see them is to watch the bus as they are emitted.
Because this is the shared standard, it works the same on KDE, GNOME, XFCE, and others.

This module runs ``dbus-monitor`` on the session bus, filtered to ``Notify`` calls, parses
each one (app, summary, body) and appends it to a capped JSON-lines ring buffer that the
``notifications`` skill reads. Stdlib only, and a graceful no-op if dbus-monitor or a session
bus is not available. A background thread keeps the monitor alive across bus restarts.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notifications.jsonl")
MAX_KEEP = 200  # ring-buffer size; notifications can carry sensitive text, so we don't hoard

# dbus-monitor prints each top-level argument at 3-space indentation; nested array/dict
# contents (the hints map) are indented further. The Notify signature is
#   (s app_name, u replaces_id, s app_icon, s summary, s body, as actions, a{sv} hints, i)
# so the only TOP-LEVEL strings, in order, are [app_name, app_icon, summary, body].
_TOP_STRING = re.compile(r'^   string "(.*)"\s*$')
_HEADERS = ("method call", "method return", "signal", "error")

_lock = threading.Lock()


def _append(rec: dict) -> None:
    """Append one record and trim the file to the last MAX_KEEP lines (atomic replace)."""
    with _lock:
        lines: list[str] = []
        try:
            with open(PATH) as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            pass
        lines.append(json.dumps(rec, ensure_ascii=False))
        lines = lines[-MAX_KEEP:]
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, PATH)


def read(since: float | None = None, limit: int = 50) -> list[dict]:
    """Recent notifications, oldest→newest. If `since` is given, only those after that ts."""
    out: list[dict] = []
    try:
        with open(PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since is not None and rec.get("ts", 0) <= since:
                    continue
                out.append(rec)
    except FileNotFoundError:
        return []
    return out[-limit:]


def clear() -> int:
    """Drop all recorded notifications; returns how many were cleared."""
    with _lock:
        n = len(read(limit=MAX_KEEP))
        try:
            os.remove(PATH)
        except FileNotFoundError:
            pass
        return n


def available() -> bool:
    return shutil.which("dbus-monitor") is not None and bool(
        os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("DISPLAY")
    )


def _emit(strings: list[str]) -> None:
    # strings == [app_name, app_icon, summary, body]; need at least summary.
    if len(strings) < 3:
        return
    app = strings[0]
    summary = strings[2] if len(strings) > 2 else ""
    body = strings[3] if len(strings) > 3 else ""
    if summary or body:
        _append({"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "app": app, "summary": summary, "body": body})


def _parse_stream(proc: subprocess.Popen) -> None:
    """Read dbus-monitor stdout, emit a record per Notify call.

    A block is emitted as soon as its 4 top-level strings are in (Notify always sends
    app_name/app_icon/summary/body), so the newest notification isn't left buffered waiting
    for the next bus message. The header path flushes any partial leftover defensively.
    """
    capturing = False
    strings: list[str] = []

    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if line.startswith(_HEADERS):  # a new message begins
            if capturing and strings:   # partial block (shouldn't usually happen) — salvage it
                _emit(strings)
            capturing = (line.startswith("method call")
                         and "member=Notify" in line
                         and "org.freedesktop.Notifications" in line)
            strings = []
            continue
        if capturing:
            m = _TOP_STRING.match(line)
            if m:
                strings.append(m.group(1))
                if len(strings) == 4:   # full Notify payload captured — emit now
                    _emit(strings)
                    capturing = False
                    strings = []
    if capturing and strings:
        _emit(strings)


def _run() -> None:
    """Keep a dbus-monitor running, restarting it if the bus drops."""
    argv = ["dbus-monitor", "--session",
            "interface='org.freedesktop.Notifications',member='Notify'"]
    while True:
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True)
            _parse_stream(proc)
        except Exception:  # noqa: BLE001 - monitor crashed; back off and retry
            pass
        time.sleep(3)


def start() -> bool:
    """Launch the recorder in a daemon thread. Returns False if it can't run here."""
    if not available():
        return False
    threading.Thread(target=_run, name="notify-recorder", daemon=True).start()
    return True

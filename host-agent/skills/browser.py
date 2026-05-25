"""Browser skill: search YouTube and play in real Google Chrome, driven over CDP.

"Play X on YouTube" launches the system Google Chrome (with a dedicated profile and
remote-debugging enabled), searches YouTube for X, and plays the first result. It's
driven by a detached worker (browser_play.py) running in the project venv
(host-agent/.venv), so the stdlib-only agent core stays clean and the browser keeps
playing after the call returns. A new play replaces the previous one.

We drive *real* Chrome over CDP rather than Playwright's bundled browser: Chrome is
launched without --enable-automation, so navigator.webdriver stays false and YouTube
treats it as an ordinary browser instead of blocking it. Playwright is used only as the
CDP client (selectors/waits) — install it once via scripts/setup-browser.sh.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time

from ._util import fail, ok
from .registry import skill

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_PY = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_WORKER = os.path.join(_AGENT_DIR, "browser_play.py")
_PIDFILE = os.path.join(_AGENT_DIR, "youtube.pid")
# Control channel to the live worker (see browser_play.py): we append JSON commands here
# and read the worker's published playback state back.
_CMD_FILE = os.path.join(_AGENT_DIR, "youtube.cmd")
_STATE_FILE = os.path.join(_AGENT_DIR, "youtube.state")


_CHROME_BIN = os.environ.get("AETHER_CHROME_BIN", "/usr/bin/google-chrome")


def _ready() -> bool:
    return (
        os.path.exists(_VENV_PY)
        and os.path.exists(_WORKER)
        and os.path.exists(_CHROME_BIN)
    )


def _reaped(pid: int) -> bool:
    """True once `pid` has exited (reaps it if it's our child). Never blocks. Handles the
    case where the agent was restarted and the worker is no longer our child."""
    try:
        return os.waitpid(pid, os.WNOHANG)[0] != 0
    except ChildProcessError:
        try:
            os.kill(pid, 0)   # not our child — is it still alive at all?
            return False
        except (ProcessLookupError, PermissionError):
            return True
    except OSError:
        return True


def _stop() -> None:
    try:
        with open(_PIDFILE) as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        pgid = None
    if pgid is not None:
        # Graceful (lets Chrome close cleanly), then forceful — bounded so we never hang.
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pgid = None
    deadline = time.time() + 5
    while pgid is not None and time.time() < deadline:
        if _reaped(pid):
            pgid = None
            break
        time.sleep(0.15)
    if pgid is not None:  # still alive after grace → kill the group hard
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(20):
            if _reaped(pid):
                break
            time.sleep(0.1)
    try:
        os.remove(_PIDFILE)
    except FileNotFoundError:
        pass
    for path in (_CMD_FILE, _STATE_FILE):  # tear down the control channel too
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _alive() -> bool:
    """True while the worker (and thus the Chrome playback) is still running."""
    try:
        with open(_PIDFILE) as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # alive, just not ours (agent was restarted)
        return True
    except OSError:
        return False
    return True


def _send(cmd: dict) -> None:
    """Queue one control command for the worker to apply to the live video."""
    with open(_CMD_FILE, "a") as f:
        f.write(json.dumps(cmd) + "\n")


def _state() -> dict | None:
    try:
        with open(_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


@skill("play_youtube")
def play_youtube(params):
    query = str(params.get("query") or params.get("text") or "").strip()
    if not query:
        return fail("What should I play on YouTube?")
    if not _ready():
        return fail("Browser playback isn't set up yet — run scripts/setup-browser.sh on the host.")
    _stop()  # replace any current playback
    proc = subprocess.Popen(
        [_VENV_PY, _WORKER, query],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    with open(_PIDFILE, "w") as f:
        f.write(str(proc.pid))
    return ok(f"Opening YouTube in Chrome and playing “{query}”.", query=query)


@skill("stop_youtube")
def stop_youtube(_):
    _stop()
    return ok("Closed the YouTube playback.")


@skill("youtube_volume")
def youtube_volume(params):
    """Volume of the YouTube video itself (the Chrome playback), independent of system volume."""
    if not _alive():
        return fail("Nothing is playing on YouTube right now.")
    if "action" in params:
        action = str(params["action"]).lower()
        if action in ("mute", "unmute"):
            _send({"action": action})
            return ok("Muted YouTube." if action == "mute" else "Unmuted YouTube.")
        if action in ("up", "down"):
            cur = int((_state() or {}).get("volume", 50))
            level = max(0, min(100, cur + (10 if action == "up" else -10)))
            _send({"action": "volume", "level": level})
            return ok(f"YouTube volume {'up' if action == 'up' else 'down'} to {level} percent.", level=level)
        return fail(f"Unknown volume action '{action}'.")
    level = max(0, min(100, int(params.get("level", 50))))
    _send({"action": "volume", "level": level})
    return ok(f"YouTube volume set to {level} percent.", level=level)


_YT_ACTIONS = {"play", "pause", "playpause", "next", "restart"}


@skill("youtube_control")
def youtube_control(params):
    """Transport control for the playing YouTube video (pause/resume/next/restart/seek)."""
    if not _alive():
        return fail("Nothing is playing on YouTube right now.")
    action = str(params.get("action", "playpause")).lower()
    if action == "seek":
        try:
            secs = int(params.get("seconds", 0))
        except (TypeError, ValueError):
            return fail("How many seconds should I skip?")
        _send({"action": "seek", "seconds": secs})
        verb = "Skipped forward" if secs >= 0 else "Went back"
        return ok(f"{verb} {abs(secs)} seconds.")
    if action not in _YT_ACTIONS:
        return fail(f"Unknown YouTube action '{action}'.")
    _send({"action": action})
    msg = {"play": "Resumed the video.", "pause": "Paused the video.",
           "playpause": "Toggled play/pause.", "next": "Skipping to the next video.",
           "restart": "Back to the start."}[action]
    return ok(msg)


@skill("youtube_status")
def youtube_status(_):
    """Report what's playing on YouTube right now (title, playing/paused, volume)."""
    if not _alive():
        return ok("Nothing is playing on YouTube right now.", playing=False)
    st = _state() or {}
    if not st.get("ready"):
        return ok("YouTube is still loading.", playing=False)
    title = (st.get("title") or "").strip()
    playing = bool(st.get("playing"))
    vol = st.get("volume")
    what = f"“{title}”" if title else "a video"
    state_word = "Playing" if playing else "Paused"
    extra = f" at {vol} percent volume" if isinstance(vol, int) else ""
    return ok(f"{state_word} {what}{extra}.", playing=playing, title=title, volume=vol)

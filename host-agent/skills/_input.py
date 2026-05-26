"""Keyboard input simulation that does NOT trigger the desktop portal popup.

On KDE Wayland, simulating input with xdotool goes through XWayland's XTEST, which the
compositor gates behind the "an app requests remote control: input devices" portal prompt —
an interruption during a perfectly normal action. ydotool instead injects events straight
into the kernel via /dev/uinput (through ydotoold), which the compositor sees as a real
device, so there is NO portal prompt.

So: prefer ydotool when its daemon socket is reachable, and fall back to xdotool otherwise.
Set up ydotoold once with scripts/setup-input.sh; without it we degrade to xdotool (which
still works, but may show the portal prompt).
"""
from __future__ import annotations

import os

from ._util import has, run

# Candidate ydotoold socket paths, in priority order. scripts/setup-input.sh creates the
# /run one (a system daemon, perm 0666). The others cover ydotool's own defaults.
_SOCKET_CANDIDATES = [
    os.environ.get("AETHER_YDOTOOL_SOCKET", ""),
    os.environ.get("YDOTOOL_SOCKET", ""),
    "/run/ydotoold/socket",
    os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/user/%d" % os.getuid()), ".ydotool_socket"),
    "/tmp/.ydotool_socket",
]

# xdotool/X-style key names → Linux evdev key codes (linux/input-event-codes.h), so we can
# drive ydotool (which speaks raw codes). Covers modifiers, letters, digits, navigation,
# function and the common punctuation/media keys an agent actually sends.
_EVDEV: dict[str, int] = {
    "ctrl": 29, "control": 29, "leftctrl": 29, "rightctrl": 97,
    "alt": 56, "leftalt": 56, "rightalt": 100, "altgr": 100,
    "shift": 42, "leftshift": 42, "rightshift": 54,
    "super": 125, "meta": 125, "win": 125, "cmd": 125, "leftmeta": 125, "rightmeta": 126,
    "esc": 1, "escape": 1, "tab": 15, "enter": 28, "return": 28, "space": 57,
    "backspace": 14, "delete": 111, "del": 111, "insert": 110, "ins": 110,
    "home": 102, "end": 107, "pageup": 104, "prior": 104, "pagedown": 109, "next": 109,
    "up": 103, "down": 108, "left": 105, "right": 106, "capslock": 58,
    "minus": 12, "equal": 13, "plus": 13, "comma": 51, "period": 52, "dot": 52, "slash": 53,
    "backslash": 43, "semicolon": 39, "apostrophe": 40, "grave": 41,
    "bracketleft": 26, "bracketright": 27,
    "mute": 113, "volumedown": 114, "volumeup": 115,
    "playpause": 164, "nextsong": 163, "prevsong": 165, "stop": 166,
}
# letters a–z (KEY_A starts at 30 in qwerty row order, so map explicitly)
_EVDEV.update({c: code for c, code in zip(
    "abcdefghijklmnopqrstuvwxyz",
    [30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44])})
# digits 0–9
_EVDEV.update({"0": 11, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10})
# function keys f1–f12
_EVDEV.update({f"f{n}": code for n, code in zip(range(1, 13), [59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88])})


def _ydotool_env() -> dict | None:
    """Return an environment with YDOTOOL_SOCKET set to a live daemon socket, or None."""
    if not has("ydotool"):
        return None
    for path in _SOCKET_CANDIDATES:
        if path and os.path.exists(path):
            env = dict(os.environ)
            env["YDOTOOL_SOCKET"] = path
            return env
    return None


def _codes_for(combo: str) -> list[int] | None:
    """Map an 'ctrl+alt+t' style combo to evdev key codes, or None if any key is unknown."""
    codes = []
    for part in combo.replace(" ", "").split("+"):
        if not part:
            continue
        code = _EVDEV.get(part.lower())
        if code is None:
            return None
        codes.append(code)
    return codes or None


def _run_ydotool(argv: list[str], env: dict):
    import subprocess
    try:
        p = subprocess.run(["ydotool", *argv], input=None, capture_output=True, text=True,
                           timeout=15, env=env)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", "ydotool: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def send_keys(combo: str) -> tuple[bool, str]:
    """Send a key chord (e.g. 'ctrl+alt+t'). Prefers ydotool (no portal popup)."""
    env = _ydotool_env()
    codes = _codes_for(combo) if env else None
    if env and codes:
        # press modifiers→key in order, then release in reverse: '29:1 20:1 20:0 29:0'
        seq = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
        rc, _, err = _run_ydotool(["key", *seq], env)
        if rc == 0:
            return True, ""
    if has("xdotool"):
        rc, _, err = run(["xdotool", "key", "--clearmodifiers", "--", combo])
        return (rc == 0), err
    return False, "no input tool available (install ydotool or xdotool)"


def type_text(text: str) -> tuple[bool, str]:
    """Type a string. Prefers ydotool (no portal popup)."""
    env = _ydotool_env()
    if env:
        rc, _, err = _run_ydotool(["type", "--", text], env)
        if rc == 0:
            return True, ""
    if has("xdotool"):
        rc, _, err = run(["xdotool", "type", "--clearmodifiers", "--", text])
        return (rc == 0), err
    return False, "no input tool available (install ydotool or xdotool)"


def available() -> bool:
    return _ydotool_env() is not None or has("xdotool")

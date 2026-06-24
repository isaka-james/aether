"""Window, tab and keyboard skills.

Window listing/closing/focusing uses KWin scripting on KDE (so it sees native Wayland
windows) and falls back to wmctrl on other desktops (GNOME, XFCE, and so on, over X11 or
XWayland). Tab/keystroke skills use ydotool or xdotool and are best-effort.
"""
from __future__ import annotations

import difflib
import re

from . import _input, kwin
from ._util import fail, ok
from .registry import skill

_label = kwin.label


def _closest_window(title: str, wins: list[dict]) -> str | None:
    """The open window most like `title` — compared against its caption, friendly app name, and
    window class — tolerant of small speech-to-text errors. Returns a substring that
    ``act_on_window`` can re-match (the winning window's caption/class), or None below threshold."""
    low = (title or "").strip().lower()
    if not low or not wins:
        return None
    best, best_score = None, 0.0
    for w in wins:
        cap = w.get("title") or ""
        # Compare against the friendly app name, the window class, the whole caption, AND each
        # word of the caption — so a short, typo'd request ("gmial") still matches a long title.
        cands = [_label(w.get("app") or ""), w.get("app") or "", cap, *re.findall(r"[A-Za-z0-9]+", cap)]
        score = max((difflib.SequenceMatcher(None, low, c.lower()).ratio() for c in cands if c),
                    default=0.0)
        if score > best_score:
            best, best_score = w, score
    if best is None or best_score < 0.6:
        return None
    return (best.get("title") or best.get("app") or "").strip() or None


def _fuzzy_window(title: str) -> str | None:
    return _closest_window(title, kwin.list_windows())


@skill("list_windows")
def list_windows(_):
    wins = kwin.list_windows()
    if not wins:
        return ok("No open windows were found.", windows=[], count=0)
    desc = [f"{_label(w['app'])}: {w['title']}" if w["title"] else _label(w["app"]) for w in wins]
    return ok(f"{len(wins)} open window(s): " + "; ".join(desc) + ".", windows=wins, count=len(wins))


@skill("count_windows")
def count_windows(_):
    wins = kwin.list_windows()
    apps = sorted({_label(w["app"]) for w in wins})
    return ok(f"You have {len(wins)} open window(s) across: " + ", ".join(apps) + "."
              if wins else "You have no windows open.", count=len(wins), apps=apps)


@skill("close_window")
def close_window(params):
    title = str(params.get("title", "")).strip()
    if not title:
        return fail("Which window should I close? Give me part of its title.")
    n = kwin.act_on_window(title, "close")
    if not n:  # nothing matched literally — try the closest open window (tolerates typos/STT)
        alt = _fuzzy_window(title)
        if alt and alt.lower() != title.lower():
            n = kwin.act_on_window(alt, "close")
            if n:
                return ok(f"Closed {n} window(s) matching '{alt}'.", closed=n, matched_from=title)
    return ok(f"Closed {n} window(s) matching '{title}'.", closed=n) if n \
        else fail(f"No window matched '{title}'.")


@skill("focus_window")
def focus_window(params):
    title = str(params.get("title", "")).strip()
    if not title:
        return fail("Which window should I switch to?")
    n = kwin.act_on_window(title, "focus")
    if not n:  # nothing matched literally — try the closest open window (tolerates typos/STT)
        alt = _fuzzy_window(title)
        if alt and alt.lower() != title.lower():
            n = kwin.act_on_window(alt, "focus")
            if n:
                return ok(f"Switched to '{alt}'.", matched=n, matched_from=title)
    return ok(f"Switched to '{title}'.", matched=n) if n else fail(f"No window matched '{title}'.")


# Tab/keystroke skills inject input. They go through _input, which prefers ydotool (kernel
# uinput — no desktop "remote control" portal prompt) and falls back to xdotool.
@skill("close_tab")
def close_tab(_):
    okk, err = _input.send_keys("ctrl+w")
    return ok("Closed the current tab.") if okk else fail("Couldn't close the tab.", error=err)


@skill("new_tab")
def new_tab(_):
    okk, err = _input.send_keys("ctrl+t")
    return ok("Opened a new tab.") if okk else fail("Couldn't open a tab.", error=err)


@skill("press_keys")
def press_keys(params):
    keys = str(params.get("keys", "")).strip()
    if not keys:
        return fail("Which keys should I press?")
    okk, err = _input.send_keys(keys)
    return ok(f"Pressed {keys}.", keys=keys) if okk else fail("Couldn't send those keys.", error=err)


@skill("type_text")
def type_text(params):
    text = str(params.get("text", ""))
    if not text:
        return fail("What should I type?")
    okk, err = _input.type_text(text)
    return ok("Typed it.") if okk else fail("Couldn't type that.", error=err)

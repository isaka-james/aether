"""Window, tab and keyboard skills.

Window listing/closing/focusing uses **KWin scripting** so it works on Wayland (sees
native Wayland windows, not just XWayland). Tab/keystroke skills use xdotool, which is
XWayland-only and best-effort.
"""
from __future__ import annotations

from . import kwin
from ._util import fail, need_tool, ok, run
from .registry import skill

_label = kwin.label


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
    return ok(f"Closed {n} window(s) matching '{title}'.", closed=n) if n \
        else fail(f"No window matched '{title}'.")


@skill("focus_window")
def focus_window(params):
    title = str(params.get("title", "")).strip()
    if not title:
        return fail("Which window should I switch to?")
    n = kwin.act_on_window(title, "focus")
    return ok(f"Switched to '{title}'.", matched=n) if n else fail(f"No window matched '{title}'.")


@skill("close_tab")
def close_tab(_):
    if (miss := need_tool("xdotool", "Closing a tab")):
        return miss
    rc, _, err = run(["xdotool", "getactivewindow", "key", "--clearmodifiers", "ctrl+w"])
    return ok("Closed the current tab.") if rc == 0 else fail("Couldn't close the tab.", error=err)


@skill("new_tab")
def new_tab(_):
    if (miss := need_tool("xdotool", "Opening a tab")):
        return miss
    rc, _, err = run(["xdotool", "getactivewindow", "key", "--clearmodifiers", "ctrl+t"])
    return ok("Opened a new tab.") if rc == 0 else fail("Couldn't open a tab.", error=err)


@skill("press_keys")
def press_keys(params):
    if (miss := need_tool("xdotool", "Pressing keys")):
        return miss
    keys = str(params.get("keys", "")).strip()
    if not keys:
        return fail("Which keys should I press?")
    rc, _, err = run(["xdotool", "key", "--clearmodifiers", "--", keys])
    return ok(f"Pressed {keys}.", keys=keys) if rc == 0 else fail("Couldn't send those keys.", error=err)


@skill("type_text")
def type_text(params):
    if (miss := need_tool("xdotool", "Typing text")):
        return miss
    text = str(params.get("text", ""))
    if not text:
        return fail("What should I type?")
    rc, _, err = run(["xdotool", "type", "--clearmodifiers", "--", text])
    return ok("Typed it.") if rc == 0 else fail("Couldn't type that.", error=err)

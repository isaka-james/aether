"""Clipboard: read what's on the clipboard, or copy text to it.

Works on Wayland (wl-clipboard) and X11 (xclip / xsel), trying whichever is installed.
"""
from __future__ import annotations

from ._util import fail, has, ok, run
from .registry import skill

# (tool argv) for reading and for writing (writing takes the text on stdin).
_GET = (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"], ["xsel", "-b", "-o"])
_SET = (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"])


@skill("clipboard")
def clipboard(params):
    """Read or set the clipboard. {"action": "get"} or {"action": "set", "text": "..."}."""
    action = str(params.get("action", "get")).lower()
    if action == "get":
        for argv in _GET:
            if has(argv[0]):
                rc, out, _ = run(argv)
                if rc == 0:
                    return ok(f"On the clipboard: {out.strip()[:300]}" if out.strip()
                              else "The clipboard is empty.", text=out)
        return fail("No clipboard tool is installed. Install wl-clipboard or xclip.")
    if action == "set":
        text = str(params.get("text", ""))
        if not text:
            return fail("What should I copy to the clipboard?")
        for argv in _SET:
            if has(argv[0]):
                rc, _, err = run(argv, stdin=text)
                if rc == 0:
                    return ok("Copied to the clipboard.")
        return fail("No clipboard tool is installed. Install wl-clipboard or xclip.")
    return fail("Use action 'get' to read the clipboard or 'set' to copy text to it.")

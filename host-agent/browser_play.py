#!/usr/bin/env python3
"""YouTube playback in real Google Chrome, driven over CDP.

We launch the system Google Chrome ourselves with --remote-debugging-port (and a
dedicated persistent profile), then attach to it with Playwright's connect_over_cdp.
Chrome is started WITHOUT --enable-automation, so navigator.webdriver stays false and
YouTube treats it as an ordinary browser — this is the whole point of attaching to real
Chrome instead of a bundled automation browser, which YouTube readily blocks.

Playwright is used only as a convenient CDP client (selectors, waits) — it connects to
the already-running Chrome and never downloads or launches a browser of its own. The
browser itself is your normal Chrome. A new play replaces the previous one (see
skills/browser.py).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import quote_plus
import urllib.request

from playwright.sync_api import sync_playwright

CHROME_BIN = os.environ.get("AETHER_CHROME_BIN", "/usr/bin/google-chrome")
PROFILE = os.path.expanduser("~/.cache/aether/chrome")

# Control channel shared with skills/browser.py: the skill appends one JSON command per
# line to CMD_FILE (volume / pause / seek / …); this worker drains and applies them to the
# live page, and publishes the current playback state to STATE_FILE for status queries.
_HERE = os.path.dirname(os.path.abspath(__file__))
CMD_FILE = os.path.join(_HERE, "youtube.cmd")
STATE_FILE = os.path.join(_HERE, "youtube.state")

# Read the playing <video>'s state. Runs in the page; tolerant of the element not existing
# yet (during navigation/ads the title still resolves).
JS_STATE = """() => {
  const v = document.querySelector('video');
  const title = (document.title || '').replace(/\\s*-\\s*YouTube\\s*$/, '').trim();
  if (!v) return {ready: false, title};
  return {ready: true, playing: !v.paused, muted: !!v.muted,
          volume: Math.round((v.muted ? 0 : v.volume) * 100),
          currentTime: Math.round(v.currentTime || 0),
          duration: Math.round(v.duration || 0), title};
}"""

_pw = None
_browser = None
_chrome = None
_ctx = None  # the CDP browser context; used to (re)acquire a working page on demand
# Outcome of the most recent play (initial launch or a play_query command). Published into
# STATE_FILE under "play" so the skill can CONFIRM a video actually loaded — and read back the
# real title now on screen — instead of assuming a queued command succeeded. Shape:
#   {"req": <id|"init">, "query": <str>, "ok": <bool>, "title": <str>, "ts": <float>}
_play_result: dict | None = None

# Title of the playing video, stripped of YouTube's suffix — used to confirm what's on screen.
JS_TITLE = "() => (document.title || '').replace(/\\s*-\\s*YouTube\\s*$/, '').trim()"


def _shutdown(*_):
    global _pw, _browser, _chrome
    try:
        if _browser is not None:
            _browser.close()  # detaches CDP; does not kill Chrome
    except Exception:
        pass
    try:
        if _pw is not None:
            _pw.stop()
    except Exception:
        pass
    try:
        if _chrome is not None and _chrome.poll() is None:
            _chrome.terminate()
            try:
                _chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _chrome.kill()
    except Exception:
        pass
    sys.exit(0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_cdp(port: int, timeout: float = 30.0) -> str:
    """Poll Chrome's CDP HTTP endpoint until the websocket URL is available."""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/json/version"
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                import json

                data = json.load(r)
                ws = data.get("webSocketDebuggerUrl")
                if ws:
                    return f"http://127.0.0.1:{port}"
        except Exception as e:  # noqa: BLE001 - Chrome not up yet
            last_err = e
        time.sleep(0.3)
    raise RuntimeError(f"Chrome CDP did not come up on :{port} ({last_err})")


def _launch_chrome(port: int, start_url: str) -> subprocess.Popen:
    os.makedirs(PROFILE, exist_ok=True)
    argv = [
        CHROME_BIN,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--start-maximized",
        # NB: intentionally NO --enable-automation / --headless — keeps webdriver=false
        start_url,
    ]
    return subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


def _get_page():
    """Return a usable page in the live Chrome — reusing an open tab, or opening one. This is
    what lets a new play swap the video in the SAME browser instead of relaunching Chrome."""
    global _ctx
    if _ctx is None:
        return None
    try:
        open_pages = [p for p in _ctx.pages if not p.is_closed()]
    except Exception:  # noqa: BLE001 - context tearing down
        return None
    if open_pages:
        return open_pages[0]
    try:
        return _ctx.new_page()
    except Exception:  # noqa: BLE001
        return None


def _search_and_play(query: str) -> bool:
    """Search YouTube for `query` in the existing page and play the first result. Reused for
    both the initial play and every subsequent 'play something else' on the same session."""
    query = (query or "").strip()
    if not query:
        return False
    page = _get_page()
    if page is None:
        return False
    target = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    cur = page.url or ""
    # Skip the navigation if we're already on this exact search (e.g. Chrome opened it on
    # launch); otherwise navigate the existing tab to the new search.
    if "youtube.com/results" not in cur or quote_plus(query) not in cur:
        page.goto(target, wait_until="domcontentloaded", timeout=30_000)
    _dismiss_consent(page)
    page.wait_for_selector(
        "ytd-video-renderer a#video-title, a#video-title-link",
        state="visible", timeout=20_000)
    for selector in (
        "ytd-video-renderer a#video-title",
        "a#video-title-link",
        "ytd-video-renderer a#thumbnail",
    ):
        try:
            page.locator(selector).first.click(timeout=10_000)
            break
        except Exception:
            continue
    else:
        print("Could not click a search result", flush=True)
        return False
    try:
        page.wait_for_selector("video", state="visible", timeout=30_000)
    except Exception as e:  # noqa: BLE001
        print(f"Video did not load: {e}", flush=True)
        return False
    _dismiss_consent(page)
    print(f"Playing: {query}", flush=True)
    return True


def _dismiss_consent(page, max_tries=3):
    for _ in range(max_tries):
        for label in ("Accept all", "I agree", "Reject all", "Accept the use of cookies"):
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count() and btn.first.is_visible():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(1500)
                    return
            except Exception:
                continue
        page.wait_for_timeout(800)


def _play(query: str, req: str) -> bool:
    """Search and play `query`, then record the REAL outcome (success + the title now on
    screen) in `_play_result` for the skill to read back. This is what lets play_youtube
    confirm a video actually started instead of assuming a queued command worked. Never
    raises — a failed search is recorded as ok:false, not a silent no-op."""
    global _play_result
    title = ""
    try:
        played = _search_and_play(query)
    except Exception as e:  # noqa: BLE001 - record the failure rather than crash the loop
        print(f"play failed for {query!r}: {e}", flush=True)
        played = False
    if played:
        page = _get_page()
        if page is not None:
            # The tab title can lag the navigation (still the results page) for a beat on a
            # slow box — poll briefly so we report the actual VIDEO title, not a stale one.
            for _ in range(12):
                try:
                    cand = (page.evaluate(JS_TITLE) or "").strip()
                except Exception:  # noqa: BLE001
                    cand = ""
                if cand and cand.lower() not in ("youtube", query.strip().lower()):
                    title = cand
                    break
                title = cand or title
                time.sleep(0.25)
    _play_result = {"req": req, "query": query, "ok": bool(played),
                    "title": title, "ts": time.time()}
    return bool(played)


def _apply(page, cmd: dict) -> None:
    """Apply one control command to the live <video> element."""
    action = str(cmd.get("action", "")).lower()
    if action == "volume":
        lvl = max(0, min(100, int(cmd.get("level", 50))))
        page.evaluate("(p)=>{const v=document.querySelector('video'); if(v){v.muted=false; v.volume=p/100;}}", lvl)
    elif action == "mute":
        page.evaluate("()=>{const v=document.querySelector('video'); if(v) v.muted=true;}")
    elif action == "unmute":
        page.evaluate("()=>{const v=document.querySelector('video'); if(v) v.muted=false;}")
    elif action in ("pause", "play", "playpause"):
        page.evaluate(
            "(a)=>{const v=document.querySelector('video'); if(!v) return;"
            " if(a==='pause'||(a==='playpause'&&!v.paused)) v.pause(); else v.play();}",
            action)
    elif action == "seek":
        secs = float(cmd.get("seconds", 0))
        page.evaluate("(s)=>{const v=document.querySelector('video'); if(v) v.currentTime=Math.max(0,(v.currentTime||0)+s);}", secs)
    elif action == "restart":
        page.evaluate("()=>{const v=document.querySelector('video'); if(v){v.currentTime=0; v.play();}}")
    elif action == "next":
        try:
            page.locator(".ytp-next-button").first.click(timeout=3000)
        except Exception:
            pass
    elif action in ("play_query", "search"):
        # Play something new in the SAME Chrome session — no relaunch. Record the outcome
        # (under the command's req) so the skill can confirm it actually started.
        _play(str(cmd.get("query") or ""), str(cmd.get("req") or ""))
    elif action in ("fullscreen", "fullscreen_on", "fullscreen_off"):
        # Toggle YouTube's OWN fullscreen via the player — no OS input simulation, so it
        # never triggers the KDE "remote control" portal popup. Click the player's
        # fullscreen button (its aria-pressed tells us the current state so we can honour
        # an explicit on/off); fall back to the player keyboard shortcut inside the page.
        want = {"fullscreen_on": True, "fullscreen_off": False}.get(action)
        try:
            btn = page.locator(".ytp-fullscreen-button").first
            is_full = False
            try:
                is_full = (btn.get_attribute("aria-pressed", timeout=1000) == "true")
            except Exception:
                is_full = bool(page.evaluate("() => !!document.fullscreenElement"))
            if want is None or want != is_full:
                btn.click(timeout=3000)
        except Exception:
            try:  # last resort: dispatch the player's own 'f' shortcut within the page
                page.locator("video").first.focus(timeout=1000)
                page.keyboard.press("f")
            except Exception:
                pass


def _drain_commands(page) -> None:
    """Atomically claim any queued commands and apply them in order."""
    if not os.path.exists(CMD_FILE):
        return
    proc = CMD_FILE + ".proc"
    try:
        os.rename(CMD_FILE, proc)  # atomic: appends after this go to a fresh CMD_FILE
        with open(proc) as f:
            lines = f.read().splitlines()
        os.remove(proc)
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            _apply(page, json.loads(line))
        except Exception:  # noqa: BLE001 - one bad command shouldn't stop the loop
            pass


def _publish_state(page) -> None:
    try:
        state = page.evaluate(JS_STATE)
    except Exception:  # noqa: BLE001 - page navigating/closing
        return
    if _play_result is not None:  # let the skill confirm the last play and read its real title
        state["play"] = _play_result
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def main() -> None:
    global _pw, _browser, _chrome, _ctx
    query = " ".join(sys.argv[1:]).strip() or "music"
    for stale in (CMD_FILE, STATE_FILE):  # don't report a previous play's state
        try:
            os.remove(stale)
        except FileNotFoundError:
            pass
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    search_url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"

    port = _free_port()
    print(f"Launching Chrome on CDP port {port} for: {query}", flush=True)
    _chrome = _launch_chrome(port, search_url)
    endpoint = _wait_for_cdp(port)

    _pw = sync_playwright().start()
    _browser = _pw.chromium.connect_over_cdp(endpoint)
    _ctx = _browser.contexts[0] if _browser.contexts else _browser.new_context()

    # Wait for Chrome's initial tab to exist, then play the first query in it, recording the
    # outcome under req "init" so the skill can confirm it. We do NOT exit on a failed first
    # search: the Chrome session stays up so the skill can report the failure honestly and the
    # agent can retry a cleaner query into the SAME browser.
    deadline = time.time() + 10
    while time.time() < deadline and not _get_page():
        time.sleep(0.2)
    _play(query, "init")

    # Keep the process (and thus Chrome) alive until stopped, servicing control commands —
    # volume / pause / seek / next / fullscreen, and play_query to swap in a NEW video on the
    # same session — and publishing playback state for status queries. The page is re-acquired
    # each tick so the loop survives navigations and a closed/reopened tab.
    while True:
        # If Chrome is gone — the user closed the window, or it crashed (its child goes
        # <defunct>) — exit instead of lingering. A worker that outlives its browser still
        # looks "alive" to the skill (the pid exists), so it would silently swallow every new
        # play_query forever, never confirming and never dying. Shutting down here lets the
        # next play_youtube start a clean, fresh session.
        if _chrome.poll() is not None or not _browser.is_connected():
            print("Chrome is gone; shutting down worker.", flush=True)
            _shutdown()
        page = _get_page()
        if page is not None:
            _drain_commands(page)
            _publish_state(page)
        time.sleep(0.5)


if __name__ == "__main__":
    main()

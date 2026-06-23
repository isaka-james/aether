"""The brain: an agentic, multi-step loop.

For each request the model runs a ReAct-style loop: it calls a tool (skill), reads the
result, and decides the next step — chaining several read-only tools to investigate
before answering, or performing an action. It ends by emitting a final spoken answer.

  user ─▶ [LLM picks a tool] ─▶ safety screen ─▶ host agent ─▶ OBSERVATION ─▶ loop…
       └────────────────────────────── {"final": "..."} ──────────────────────────▶ speak

Safety:
  • Structured skills (bluetooth_power, list_windows, …) run automatically.
  • run_command is classified: block → fed back as an error; allow → runs; confirm/root →
    the loop pauses and asks the user to approve (then execute_approved runs it one-shot).

An optional on_progress(step, label) async callback drives the web client's phase UI.
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from . import cache, db, host_client, llm, tts
from .config import get_settings
from .models import Action, Clarification, CommandResult
from .safety import classify_command
from .skills import SKILL_NAMES, catalog_for_prompt

# Tools answered inside the backend (data layer), not dispatched to the host agent. Checked
# before SKILL_NAMES so they don't get routed to the host even though they're in the catalog.
_BACKEND_TOOLS = {"list_favorites", "remember_favorite", "forget_favorite",
                  "get_preference", "set_preference", "play_history"}

log = logging.getLogger("aether.orchestrator")

Progress = Optional[Callable[[str, str], Awaitable[None]]]
_SUDO = re.compile(r"\bsudo\b")
# A capable cloud model (DeepSeek) drives the loop, so give it room to investigate state,
# resolve a conflict, act, and verify — a smart multi-step chain shouldn't get truncated.
MAX_STEPS = 9
OBS_LIMIT = 4000  # generous enough that a long multi-item observation isn't chopped mid-JSON
# Voiced when the model returns nothing usable — better a graceful line than silence.
FALLBACK_REPLY = "I'm afraid I've come up short on that one, sir."

# Statuses that represent the agent talking — a finished answer (done), a question
# (needs_choice), or an approval prompt (needs_confirmation). Everything else (error,
# blocked) is an infrastructure failure that surfaces on the web as a detailed flash but
# stays silent on the speakers. The gate is STATUS only: `ok` may legitimately be False
# on a needs_confirmation prompt or on a curated "done" reply where the underlying tool
# failed — the agent still needs its voice.
_SPEAK_STATUSES = {"done", "needs_choice", "needs_confirmation"}


def _should_speak(result: "CommandResult") -> bool:
    """True when the result is something Aether says aloud — not a system-error flash."""
    if result.status not in _SPEAK_STATUSES:
        log.info("speak skipped: status=%s (silent: error/block flashes on web only).",
                 result.status)
        return False
    if not (result.summary or "").strip():
        log.info("speak skipped: empty summary (status=%s, skill=%s).",
                 result.status, result.skill)
        return False
    return True

AGENT_SYSTEM = """You are Aether, a capable assistant that controls a Linux desktop computer.{user}

Right now it is {context}. Treat this as ground truth for anything time- or date-related
(what day it is, "this morning/tonight", how long until something) — never guess the date,
and tailor greetings and phrasing to the actual time of day.

{machine}

Reach the user's goal by calling tools — ONE per turn. Each turn output ONE JSON object:
  • {"thought":"<brief private reasoning / plan>", "tool":"<name>","params":{...}}   to run a tool
  • {"choice":{"question":"<short question>","options":["A","B","C"]}}   to ask the user
  • {"final":"<spoken answer>"}         when you are done
The "thought" is optional, never spoken, and may accompany a tool call: use it to plan.
A "choice" MUST carry 2–4 concrete, distinct options — never an empty list and never a
bare question. If you have no real options to offer, do NOT ask: act instead.

After each tool call you get an OBSERVATION with its result; use it to decide the next
step. You may chain several tools. Investigate with read-only tools before answering.

Plan before you act on anything non-trivial. On the first turn of a multi-step request,
use "thought" to lay out the steps you'll take and what you need to find out; then work the
plan one tool at a time, revising as observations come in. For a simple, unambiguous request,
skip the ceremony and just do it.

You are a real agent and you are trusted — act on your own initiative; don't stall, don't
hand back half-done work, and never reply with nothing. Prefer acting over asking. Use
"choice" ONLY at a genuine fork you cannot resolve from context — a real ambiguity (e.g.
several apps named "studio" are installed). Give 2–4 concrete options. Do NOT ask for trivia
or to confirm the obvious. (The ONE exception where the user is asked to approve before
something runs is a risky or root/sudo shell command: just issue it via run_command — the
system routes it to the user for one tap of approval automatically. You never need to ask
"shall I?" yourself for that; issue the command and it gets handled.)

Be genuinely intelligent — anticipate and handle the whole situation, don't just react to
the literal words. These principles apply to EVERY skill, not only the examples below:
- Read the state before you act when an action could collide with what's already happening.
  (Playing a new thing on YouTube does NOT collide — play_youtube swaps the new video into the
  same Chrome, so just call it; no need to stop first. The collision to avoid is local music
  left running: if play_music is going and you're about to start something else, stop_playback
  first.) Before launching an app, check is_running / running_apps and just focus_window it if
  it's already open instead of spawning a duplicate. Before setting a level, knowing the
  current one lets you make a sensible change rather than a jarring jump.
- Fulfil the whole goal in one loop. If doing it properly takes two or three steps —
  quiet the current track → play the new one; find the window → focus it → act in it;
  recall a saved preference → apply it — chain those tools yourself and finish the job.
  Never hand back a half-done task or narrate "I will now…"; just do it.
- Route the action to the right target by context. "Turn it down / it's too loud" while a
  YouTube video is playing → youtube_volume, not the system volume. "Play my favourite" →
  recall it first (list_favorites), then play it the right way. Resolve "it / that / this"
  from what's playing, what's open, or what was just discussed.
- Infer intent from imperfect speech-to-text and from context; disambiguate by looking
  rather than asking. After acting, confirm it actually took effect when sensible, then
  report what you did in ONE graceful line — not a play-by-play of every tool call.
- Adapt when something fails or surprises you — you are an agent, not a script. When a tool
  returns ok:false, an error, or nothing useful, read the OBSERVATION (including its `error`
  and `data`) to understand WHY, then decide the next move yourself: retry with better
  arguments (a shaky speech-to-text title → search again with fewer, cleaner terms), reach
  the goal another way (an app that won't launch → list what's installed and match the
  closest; a window not found → list windows and pick it), or — only when it genuinely can't
  be done — say so plainly in your own voice and, if useful, how they might fix it. Never
  parrot a raw error, never silently give up, and never repeat the identical failing call:
  change something or conclude.
- Treat failures as YOUR private problem to debug, not the user's. The user does not need a
  play-by-play of what didn't work, which tool returned what, or how many things you tried.
  Solve it. Use the OBSERVATIONS to diagnose, then quietly attempt a different angle: a
  cleaner query, a different skill, run_command with a read-only probe to see WHY a thing
  isn't where you expected, a sensible default when an input was ambiguous. Only after you
  have genuinely exhausted reasonable options should the user hear about a failure — and
  then in ONE composed sentence, with no diagnostics, no chained apologies, no "I tried X
  and Y and Z". Be the kind of attendant who handles trouble so smoothly the principal
  never has to know there was any.
- No request ends empty. You ALWAYS finish with either an action that took effect or a spoken
  {"final"} that says something real — never a blank reply, never dead air. If you reach the
  end of your reasoning with nothing done, do the most sensible thing the request implies, or
  say what you found; silence is never an acceptable outcome.
- Not everything needs a tool — you are also a knowledgeable assistant. For general questions
  (facts, explanations, how-to, advice, definitions, maths, language, a bit of conversation),
  just ANSWER directly with {"final":"..."} from your own knowledge; don't force a tool and
  don't refuse something you can simply answer. Reserve tools for acting on THIS computer or
  reading its live state (what's open/playing, the weather, the user's files). If a
  question needs current real-world facts you can't know, say so plainly rather than guessing.
- Treat the user's words as intent, not a literal spec. Speech-to-text is imperfect and the
  phrasing is often rough — reinterpret it charitably into what they most likely meant, refine
  it yourself into a clean tool query or a clear plan, and proceed. Solve it in small
  incremental steps: take one action, read the OBSERVATION, adjust, repeat until it's truly
  done. Only ask the user when intent is genuinely ambiguous and you can't settle it by looking.
- When NO named tool fits, you are not stuck — you have a full Linux shell. Discover what the
  machine can do and use it: run_command with read-only discovery first ("command -v <tool>",
  "compgen -c", "ls /usr/bin | grep -i <x>", "which <tool>") to find the right program, then
  run it (a normal command directly; a risky/root one still via run_command, which the user
  approves). If something seems off, higher-stakes, or genuinely ambiguous, raise a "choice"
  with real options instead of guessing. Anything the box can do, you can do — figure it out.

{delegation}
Recipes (these show the pattern; bring the same judgement to everything):
- "Do I have a terminal / browser / editor / <app> open?" → is_running with name set to the
  type or app ("terminal", "browser", "editor", "chrome", "code", "konsole"). is_running
  understands these categories (Alacritty/Konsole = terminal, Chrome/Chromium = browser).
- "How many windows are open?" / "what's open?" → count_windows or list_windows.
- "Is a project running?" / "what am I working on?" → list_windows (terminals/editors show
  the project path in their title), and run_command "pgrep -fa 'node|vite|npm|python|cargo|
  docker'" to find running dev servers. Combine both, then answer specifically (name the
  project from the window title if you can).
- "What's the weather / will it rain / do I need a jacket?" → weather (no args uses their
  KDE-configured location; pass location only when they name a different place). Answer the
  actual question — for "do I need a jacket / umbrella" decide from the conditions and say so.
- "Good morning" / "brief me" / "what's my day look like?" → this is a plan: greet them for
  the actual time of day, then gather the pieces that fit a briefing — the weather and
  anything pending (notifications) — and weave them into one short, natural spoken rundown.
  Don't dump raw tool output; synthesise it.
- "Play <anything>" — a song, an artist, a mood, a video, a channel's latest, a clip: ANY
  "play X" request goes to YouTube. ALWAYS use play_youtube; do NOT browse or play the local
  library even if a copy exists there — the user wants everything streamed from YouTube.
  Just call play_youtube with a CLEAN search query — it plays the first result, which is
  almost always right; trust it rather than asking which. To switch to a different song/video
  you do NOT need to stop first: calling play_youtube again SWAPS the new one into the SAME
  Chrome session (it does not relaunch the browser). Use stop_youtube only when the user
  actually wants playback to END.
    • a song → artist + title ("play blinding lights" → query "blinding lights"; "play some
      kendrick lamar gnx album" → "kendrick lamar gnx").
    • a video / channel / latest → keep the phrasing that finds it ("play mr beast latest
      video" → "mrbeast latest", "play fish13" → "fish13").
  The request is from imperfect speech-to-text: extract the real key terms, drop filler.
  CONFIRM BEFORE YOU CLAIM IT — never say something is playing on a guess. play_youtube does
  not return until it has actually searched and loaded a video; its OBSERVATION tells you the
  truth: `confirmed` (did a video really start) and `data.title` (the REAL title now on
  screen). Use them like an intelligent person who glanced at the screen before speaking:
    • confirmed:false or ok:false → it did NOT start. Do NOT say it's playing. Read the error,
      then retry with a cleaner query (e.g. "mr beast latest video" → "mrbeast latest"); after
      a second genuine failure, say plainly it wouldn't play — never pretend it did.
    • confirmed:true → judge `data.title` against what they asked. If it's clearly the wrong
      thing (the title has nothing to do with the request), youtube_control "next" to skip, or
      retry play_youtube with a sharper query. When you DO report, name what is ACTUALLY playing
      from data.title ("Now playing MrBeast's latest, …"), not a parroting of their words. If in
      any doubt about what's on screen, youtube_status to check before you speak.
  Never fall back to local music, never open a file manager or terminal for this, and never
  invent results. "Stop the music/video" → stop_youtube. (list_music / play_music exist ONLY
  for an explicit "play my LOCAL music" / "from my library/files" — otherwise always play_youtube.)
  Once a video is playing, control IT with the youtube_* tools, not the system ones:
  youtube_volume for "turn the video up/down", "make YouTube louder/quieter", "set the video
  to 40" (level 0-100, or action up/down/mute/unmute); youtube_control for "pause/resume the
  video", "skip this / next one" (next), "start it over" (restart), "skip ahead/back 30
  seconds" (action seek, seconds +/-N), and "full screen / fullscreen / make it full screen /
  exit fullscreen" (action fullscreen — this toggles YouTube's OWN fullscreen through the
  player itself, so NEVER use press_keys/type_text or shell for fullscreen); youtube_status
  for "what's playing on YouTube". The YouTube volume is SEPARATE from the system volume: use
  set_volume only for the machine's overall volume, and youtube_volume when the user means
  the video/YouTube/Chrome sound.
- "What projects do I have?" / "my <name> project" → list_projects (folders in {projects_dir}).
- "Lock / unlock the screen" → lock_screen / unlock_screen. "Suspend / sleep / hibernate /
  reboot / shut down / log out" → power_action with that exact action. Use these only on a
  clear, explicit request — never as a side effect of something else.
- "What notifications do I have / did I miss anything / read my notifications" → notifications
  (it returns what was captured live from the session bus). "Clear my notifications" →
  clear_notifications.
- Favourites & memory: "play my favourite song / a favourite" → list_favorites FIRST, then
  play the chosen one (play_youtube if it's a YouTube favourite, else play_music). "Remember
  this / save this as a favourite", or just after playing something the user clearly loved →
  remember_favorite (kind youtube|music, a spoken label, and value = the query/path to replay).
  "Forget <X> / remove that favourite" → forget_favorite. "What do I play most" → play_history.
- Remembered settings: "set my usual/favourite volume to 30", "remember I like the video at
  60" → set_preference (key like "volume" or "youtube_volume", value the number). When the
  user asks for their "usual/favourite" setting ("set it to my usual volume") → get_preference
  to recall the number, then apply it (set_volume / youtube_volume). Don't invent the value.
- For admin actions, use run_command with a command starting with "sudo" (user approves).
- Discover what's possible instead of guessing. To check what works on THIS machine call
  capabilities; to find an installed program for a need call find_tool "<keyword>" (e.g.
  "pdf", "convert") then use it. find_files locates the user's files by name; clipboard
  reads or sets the clipboard; camera takes a webcam photo; open_url opens a website, file,
  or folder. When none of these fit, fall back to run_command's read-only discovery.

Locations: the user's own files live under {projects_dir} (code projects, one dir per
project) and {music_dir} (local music). Resolve "my project(s)" / "my music" there.

Use read-only shell (ps, pgrep, ls, cat, grep, df, uptime) freely. Never invent results —
only use the OBSERVATIONs.

{capabilities}

Voice — applies to every "final" answer and every "choice" question (the text the user
hears), NOT to the tool JSON or your reasoning:
{persona}

Tools:
{catalog}"""


def _now_context() -> str:
    """Human-readable current date & time for grounding the model, e.g.
    'Tuesday, 26 May 2026, 14:32 (EAT)'. Honours AETHER_TZ; otherwise the system local zone."""
    s = get_settings()
    now = datetime.now().astimezone()
    if s.timezone:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(s.timezone))
        except Exception:  # noqa: BLE001 - bad tz name -> fall back to local
            pass
    tz = now.strftime("%Z")
    return now.strftime("%A, %d %B %Y, %H:%M") + (f" ({tz})" if tz else "")


def _user_context() -> str:
    """A short line naming who Aether is assisting and where, from AETHER_USER_* config.
    Returns "" (and the prompt reads generically) when nothing is set — open-source default."""
    s = get_settings()
    name = (s.user_name or "").strip()
    where = ", ".join(p for p in ((s.user_city or "").strip(), (s.user_country or "").strip()) if p)
    if name and where:
        return (f" You are assisting {name}, who is based in {where}. Use their name "
                "occasionally and naturally, and treat that location as 'here' for local matters.")
    if name:
        return f" You are assisting {name}. Use their name occasionally and naturally."
    if where:
        return f" The user is based in {where}; treat that location as 'here' for local matters."
    return ""


# Machine + capability awareness. One cached probe of the host agent tells the model what
# computer this is (desktop, session type, distro) and which actions actually work here (which
# tools are installed), so it is grounded and never attempts something whose tool is missing.
# Capability keys match host-agent skills/capabilities.py.
_probe_cache: dict = {"data": None, "at": 0.0}
_CAPS_TTL = 300  # seconds
_CAP_LABELS = {
    "screenshot": "taking screenshots",
    "brightness": "changing screen brightness",
    "youtube": "playing on YouTube (needs Google Chrome)",
    "local_music": "playing local music files",
    "keyboard_input": "pressing keys or typing into windows",
    "windows": "listing or controlling windows",
    "bluetooth": "Bluetooth control",
    "wifi": "Wi-Fi control",
    "power_profile": "changing the power profile",
    "input_devices": "enabling or disabling input devices",
    "camera": "taking a webcam photo",
    "clipboard": "reading or setting the clipboard",
    "media_control": "controlling the media player",
}


async def _host_probe() -> dict:
    """The host's machine + capability report, cached for _CAPS_TTL. {} if the agent is unreachable."""
    now = time.time()
    if _probe_cache["data"] is not None and now - _probe_cache["at"] < _CAPS_TTL:
        return _probe_cache["data"]
    try:
        res = await host_client.execute("capabilities", {})
        data = res.get("data") if isinstance(res, dict) else None
    except Exception as e:  # noqa: BLE001
        log.debug("host probe failed: %s", e)
        data = None
    _probe_cache["data"] = data if isinstance(data, dict) else {}
    _probe_cache["at"] = now
    return _probe_cache["data"]


async def _machine_context() -> str:
    """A grounding line naming the desktop, session type, and distro, or "" if unknown."""
    m = (await _host_probe()).get("machine")
    if not isinstance(m, dict) or not m:
        return ""
    sess = (m.get("session_type") or "").strip()
    bits = []
    if (de := (m.get("desktop") or "").strip()):
        bits.append(f"the {de} desktop")
    if sess:
        bits.append(f"a {sess} session")
    if (distro := (m.get("distro") or "").strip()):
        bits.append(distro)
    if not bits:
        return ""
    note = "This computer runs " + ", ".join(bits) + "."
    if sess.lower() == "wayland":
        note += (" It is Wayland, so simulated keystrokes and window control reach XWayland apps "
                 "and may miss native-Wayland-only windows, and toggling input devices may not "
                 "work; use the dedicated skills and don't promise what Wayland can't do.")
    return note


async def _capabilities_note() -> str:
    """A short prompt note listing actions that DON'T work here (tool missing), or "" if all do."""
    caps = (await _host_probe()).get("capabilities")
    if not isinstance(caps, dict) or not caps:
        return ""
    missing = [label for key, label in _CAP_LABELS.items() if caps.get(key) is False]
    if not missing:
        return ""
    return ("Not available on this machine right now (the needed tool isn't installed, so do "
            "NOT attempt these — tell the user plainly and that running the project's "
            "scripts/setup-desktop.sh installs the tools): " + "; ".join(missing) + ".")


async def _emit(on_progress: Progress, step: str, label: str) -> None:
    if on_progress:
        try:
            await on_progress(step, label)
        except Exception:  # noqa: BLE001
            pass


def _parse(content: str) -> dict | None:
    content = (content or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        a, b = content.find("{"), content.rfind("}")
        if a != -1 and b > a:
            try:
                return json.loads(content[a:b + 1])
            except json.JSONDecodeError:
                return None
    return None


async def _backend_tool(tool: str, params: dict) -> dict:
    """Favourites & preferences, answered from the Postgres data layer (db.py)."""
    if tool == "list_favorites":
        kind = (str(params.get("kind") or "").strip().lower()) or None
        favs = await db.list_favorites(kind=kind)
        if favs:
            names = ", ".join(f["label"] for f in favs)
            return {"ok": True, "summary": f"Favourites: {names}.", "data": {"favorites": favs}}
        top = await db.top_plays(limit=5, source=kind)
        if top:
            names = ", ".join(t["label"] for t in top if t["label"])
            return {"ok": True, "summary": f"No saved favourites yet; most played: {names}.",
                    "data": {"favorites": [], "top_plays": top}}
        return {"ok": True, "summary": "No favourites saved yet.", "data": {"favorites": []}}

    if tool == "remember_favorite":
        kind = (str(params.get("kind") or "music").strip().lower())
        label = str(params.get("label") or params.get("name") or "").strip()
        raw = params.get("value")
        value = raw if (raw is None or isinstance(raw, str)) else json.dumps(raw)
        if not label:
            return {"ok": False, "summary": "What should I save as a favourite?"}
        saved = await db.add_favorite(kind, label, value)
        return {"ok": saved,
                "summary": f"Saved “{label}” to your {kind} favourites." if saved
                else "I couldn't save that — the database is out of reach.",
                "data": {"label": label, "kind": kind}}

    if tool == "forget_favorite":
        label = str(params.get("label") or params.get("name") or "").strip()
        kind = (str(params.get("kind") or "").strip().lower()) or None
        if not label:
            return {"ok": False, "summary": "Which favourite should I remove?"}
        n = await db.remove_favorite(label, kind)
        return {"ok": n > 0,
                "summary": f"Removed “{label}” from favourites." if n
                else f"I didn't find “{label}” in your favourites.",
                "data": {"removed": n}}

    if tool == "get_preference":
        key = str(params.get("key") or "").strip()
        if not key:
            return {"ok": False, "summary": "Which preference did you mean?"}
        val = await db.get_preference(key)
        if val is None:
            return {"ok": True, "summary": f"No preference set for {key}.",
                    "data": {"key": key, "value": None}}
        return {"ok": True, "summary": f"Your {key} preference is {val}.",
                "data": {"key": key, "value": val}}

    if tool == "set_preference":
        key = str(params.get("key") or "").strip()
        if not key:
            return {"ok": False, "summary": "Which preference should I set?"}
        value = params.get("value")
        saved = await db.set_preference(key, value)
        return {"ok": saved,
                "summary": f"Noted — your {key} preference is {value}." if saved
                else "I couldn't save that preference.",
                "data": {"key": key, "value": value}}

    if tool == "play_history":
        limit = max(1, min(20, int(params.get("limit", 5) or 5)))
        source = (str(params.get("source") or "").strip().lower()) or None
        top = await db.top_plays(limit=limit, source=source)
        if not top:
            return {"ok": True, "summary": "I haven't recorded anything played yet.",
                    "data": {"top_plays": []}}
        names = ", ".join(f'{t["label"]} ({t["count"]})' for t in top if t["label"])
        return {"ok": True, "summary": f"Most played: {names}.", "data": {"top_plays": top}}

    return {"ok": False, "summary": f"unknown backend tool '{tool}'"}


async def handle(text: str, *, transcript: str | None = None,
                 clarify: "Clarification | None" = None, session: str | None = None,
                 on_progress: Progress = None) -> CommandResult:
    text = (text or "").strip()
    if not text:
        return CommandResult(ok=False, status="error",
                             summary="I'm afraid nothing came through, sir.", transcript=transcript)
    s = get_settings()
    session = session or "default"
    system = (AGENT_SYSTEM
              .replace("{context}", _now_context())
              .replace("{user}", _user_context())
              .replace("{machine}", await _machine_context())
              .replace("{capabilities}", await _capabilities_note())
              .replace("{persona}", llm.PERSONA)
              .replace("{catalog}", catalog_for_prompt())
              .replace("{delegation}", _DELEGATION_PROMPT if s.subagents_enabled else "")
              .replace("{music_dir}", s.music_dir)
              .replace("{projects_dir}", s.projects_dir))
    messages = [{"role": "system", "content": system}]
    # Short follow-up memory: replay recent turns so "and now mute it" resolves in context.
    for turn in await cache.get_context(session):
        if turn.get("content"):
            messages.append({"role": turn.get("role", "user"), "content": turn["content"]})
    messages.append({"role": "user", "content": text})
    # Resuming after the user answered a multiple-choice question: replay the question we
    # asked and their answer so the loop continues with that decision settled.
    if clarify:
        messages.append({"role": "assistant",
                         "content": json.dumps({"choice": {"question": clarify.question}})})
        messages.append({"role": "user", "content": f"My answer: {clarify.answer}"})

    trace: dict = {"skill": None}
    result = await _loop(messages, transcript, on_progress, trace=trace)
    if result.skill is None:
        result.skill = trace["skill"]
    # Never-silent guarantee for SUCCESSFUL replies only. Errors/blocks are deliberately
    # silent on the host — the user sees a detailed message in the web client instead, so
    # the agent's voice stays reserved for things it actually wants to say. The `spoken`
    # flag prevents double-speaking when _finish already played the reply.
    if s.speak_on_host and not result.spoken and _should_speak(result):
        result.spoken = await _speak(result.summary)
    # Persist (best-effort): audit log + transcript, and roll the follow-up context forward.
    try:
        await db.log_interaction(session=session, transcript=transcript, request=text,
                                 skill=trace["skill"], status=result.status,
                                 ok=result.ok, summary=result.summary)
        if result.status == "done":
            await cache.push_turn(session, text, result.summary)
    except Exception as e:  # noqa: BLE001
        log.warning("persistence failed: %s", e)
    return result


async def _loop(messages: list[dict], transcript: str | None, on_progress: Progress,
                *, trace: dict | None = None) -> CommandResult:
    s = get_settings()
    trace = trace if trace is not None else {}
    for step in range(MAX_STEPS):
        await _emit(on_progress, "thinking", "Thinking…" if step == 0 else "Reasoning about the result…")
        try:
            content = await llm.complete(messages, json_mode=True)
        except Exception as e:  # noqa: BLE001
            log.warning("agent step failed: %s", e)
            # Silent on the speakers (status=error); detailed text + `detail` is what the
            # web client renders so the user can act on the real problem.
            return await _finish(CommandResult(
                ok=False, status="error", transcript=transcript,
                summary="The language model isn't reachable just now — the request didn't get through.",
                detail=f"LLM call failed at step {step}: {type(e).__name__}: {e}",
            ), on_progress)

        obj = _parse(content)
        if not isinstance(obj, dict):
            return await _finish(_done(transcript, content or "Done."), on_progress)

        # ReAct scratchpad: a private 'thought' may accompany any turn — log it, never speak it.
        thought = obj.get("thought")
        if thought:
            log.info("agent step %d plan: %s", step, str(thought)[:400])

        choice = obj.get("choice")
        if isinstance(choice, dict) and choice.get("question"):
            options = [str(o) for o in (choice.get("options") or []) if str(o).strip()]
            if len(options) < 2:
                # A question with no real options is useless in the UI (just text + "Never
                # mind"). Don't surface it — push the model to either act or ask properly.
                messages.append({"role": "assistant", "content": json.dumps(obj)})
                messages.append({"role": "user", "content": "That choice had no options to pick "
                                 "from. Either act now with the best tool call, or re-ask with "
                                 "2-4 concrete, distinct options. Reply as JSON."})
                continue
            return await _ask_choice(transcript, str(choice["question"]), options, on_progress)

        tool = obj.get("tool") or obj.get("skill")

        if "final" in obj:
            final = (obj.get("final") or (obj.get("params") or {}).get("text") or "").strip()
            return await _finish(_done(transcript, final or FALLBACK_REPLY), on_progress)

        if not tool:
            # No action and no final answer. If the model only reasoned aloud, nudge it to act
            # rather than mistaking a bare plan for a finished reply.
            if thought:
                await _emit(on_progress, "thinking", "Planning the next step…")
                messages.append({"role": "assistant", "content": json.dumps(obj)})
                messages.append({"role": "user", "content": "Now act on that plan: reply with the "
                                 "next tool call, a choice, or your final answer as JSON."})
                continue
            final = ((obj.get("params") or {}).get("text") or content or "").strip()
            return await _finish(_done(transcript, final or FALLBACK_REPLY), on_progress)

        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        log.info("agent step %d: tool=%s params=%s", step, tool,
                 {k: v for k, v in params.items() if "password" not in k})
        if tool in ("answer", "final", "reply", "respond"):
            final = (params.get("text") or "").strip()
            return await _finish(_done(transcript, final or FALLBACK_REPLY), on_progress)
        messages.append({"role": "assistant", "content": json.dumps(obj)})

        # ---- execute the tool: delegation, safety on run_command, or a normal dispatch ----
        # Any unexpected exception here becomes a graceful OBSERVATION rather than a crash,
        # so the loop continues and the model voices the failure — a request never dies silently.
        try:
            if tool == "delegate":
                await _emit(on_progress, "thinking", "Delegating to sub-agents…")
                result: dict[str, Any] = await _delegate(params, on_progress)
            elif tool == "run_command":
                command = str(params.get("command", "")).strip()
                verdict = classify_command(command)
                if verdict.verdict == "block":
                    # Catastrophic + irreversible: refused even with approval. Hand the model a clear
                    # reason (an OBSERVATION, not a dead-end) so it explains and offers a safer route.
                    result = {"ok": False,
                              "summary": f"I can't run that — it would risk {verdict.reason}, which is "
                                         "irreversible, so I won't do it even with approval. Tell me the "
                                         "underlying goal and I'll find a safe way to get there.",
                              "data": {"refused": True, "severity": verdict.severity,
                                       "reason": verdict.reason, "command": command}}
                elif verdict.verdict == "confirm" and s.require_confirm_medium_risk:
                    root = " (needs root)" if _SUDO.search(command) else ""
                    return CommandResult(ok=False, status="needs_confirmation", transcript=transcript,
                                         skill="run_command", params={"command": command},
                                         summary=f"Approve running{root}: {command}",
                                         detail=f"This {verdict.reason}.",
                                         data={"severity": verdict.severity, "reason": verdict.reason})
                else:
                    result = await _run_and_observe(tool, params, on_progress)
            else:
                result = await _run_and_observe(tool, params, on_progress)
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s raised", tool)
            result = {"ok": False, "summary": f"The {tool} step hit an unexpected error.",
                      "data": {"error": str(e)}}

        trace["skill"] = tool
        # (media-play recording lives in _run_and_observe, so sub-agent plays are logged too.)

        # Surface the real failure detail (error/detail), not just the canned summary, so the
        # model can reason about WHY and adapt — recovery is its job, not a hardcoded branch.
        obs: dict[str, Any] = {"ok": result.get("ok"), "summary": result.get("summary"),
                               "data": result.get("data")}
        if not result.get("ok"):
            obs["error"] = result.get("error") or result.get("detail")
        observation = json.dumps(obs, ensure_ascii=False)
        messages.append({"role": "user", "content": "OBSERVATION: " + observation[:OBS_LIMIT]})

    # Out of steps — ask for a final summary from what was gathered.
    messages.append({"role": "user", "content": "Give your final answer now as {\"final\":\"...\"}."})
    try:
        obj = _parse(await llm.complete(messages, json_mode=True)) or {}
        final = obj.get("final") or "I gathered some information but couldn't fully finish."
    except Exception:  # noqa: BLE001
        final = "I gathered some information but couldn't fully finish."
    return await _finish(_done(transcript, str(final)), on_progress)


# ===========================================================================
#  Multi-agent: the coordinator (above) can hand a focused sub-goal to a
#  specialist sub-agent via the `delegate` tool, and run several in parallel.
#  Sub-agents are headless — no voice, no user questions, no approval-gated
#  shell — so the human-in-the-loop guarantees stay with the coordinator.
# ===========================================================================

# Injected into the coordinator's prompt only when sub-agents are enabled (see handle()).
_DELEGATION_PROMPT = """Delegation — you can split work across focused sub-agents with the `delegate` tool, and it is
often the smart move:
- Several independent goals at once → delegate them TOGETHER so they run in parallel. Pass a
  "tasks" list; each runs at the same time. e.g. "dim the screen, play some jazz and tell me the
  weather" → {"tool":"delegate","params":{"tasks":[
     {"role":"desktop","task":"set the screen brightness to about 30%"},
     {"role":"media","task":"play some jazz on YouTube"},
     {"role":"knowledge","task":"get the current weather for the user's location"}]}}.
- One involved, self-contained sub-goal → hand it to a specialist so your own reasoning stays
  clear: {"tool":"delegate","params":{"role":"media","task":"play blinding lights and set the video volume to 40"}}.
Roles and their focus — "media" (sound, music, YouTube, favourites & preferences), "desktop"
(windows, apps, keyboard, screen, power, connectivity), "knowledge" (weather, notifications,
files, system state, read-only lookups), "general" (the full toolset; use when unsure). Each
sub-agent investigates and acts on its own, then returns a short factual result; you weave those
into ONE spoken reply in your voice. Don't delegate a single trivial action you can do in one
call — delegation adds a round-trip; use it for parallelism or genuinely involved sub-goals.
Sub-agents can't ask the user or run approval-gated/sudo commands — keep those at your level.
"""

# What each specialist is and which tools it may use. "general" gets everything; unknown roles
# fall back to it. Tool lists are intersected with the real catalog at spawn time, so a typo or a
# retired skill simply drops out rather than breaking.
_ROLES: dict[str, tuple[str, list[str]]] = {
    "media": (
        "You handle everything to do with sound and playback: the system volume and microphone, "
        "local music, YouTube/Chrome video, and the user's media favourites & remembered settings.",
        ["set_volume", "mic", "media_control", "now_playing",
         "list_music", "play_music", "stop_playback",
         "play_youtube", "stop_youtube", "youtube_volume", "youtube_control", "youtube_status",
         "list_favorites", "remember_favorite", "forget_favorite",
         "get_preference", "set_preference", "play_history"],
    ),
    "desktop": (
        "You control the desktop: windows and apps, the keyboard and input devices, the screen "
        "(brightness, lock, screenshot, power profile) and connectivity (Bluetooth / Wi-Fi).",
        ["open_app", "open_url", "close_app", "running_apps", "is_running",
         "list_windows", "count_windows", "close_window", "focus_window", "close_tab", "new_tab",
         "press_keys", "type_text", "list_input_devices", "set_input_device", "list_projects",
         "get_brightness", "brightness", "screenshot", "lock_screen", "unlock_screen",
         "power_action", "power_profile",
         "bluetooth_status", "bluetooth_power", "wifi_status", "wifi_power", "run_command"],
    ),
    "knowledge": (
        "You gather information about the machine and the world and answer questions, using "
        "read-only tools: weather, notifications, the user's files, installed tools, system "
        "state, the clipboard and the camera. You report; you don't change settings.",
        ["weather", "notifications", "clear_notifications", "notify",
         "find_files", "find_tool", "capabilities", "system_info",
         "clipboard", "camera", "open_url", "run_command"],
    ),
    "general": (
        "You handle one focused sub-task with the full set of tools.",
        sorted(SKILL_NAMES),
    ),
}

SUBAGENT_SYSTEM = """You are a focused sub-agent of Aether, working on ONE task handed to you by the coordinator. {instruction}

Right now it is {context}.

Reach the task by calling tools — ONE per turn, as a single JSON object:
  • {"thought":"<brief private reasoning>","tool":"<name>","params":{...}}   to run a tool
  • {"final":"<concise factual result>"}                                     when the task is done
After each tool call you get an OBSERVATION; use it to choose the next step. Investigate with
read-only tools before acting when it matters. You are trusted — act on your own initiative and
finish the job; chain a few tools if that's what it takes.

You are headless: you CANNOT ask the user anything and CANNOT run commands that need approval
(no sudo, nothing destructive). Never stall or wait — do the sensible thing within your tools,
or finish and state plainly what is blocked. Never emit a "choice".

When done, reply with {"final":"..."} giving a SHORT, factual result of what you did or found —
one or two plain sentences, no persona and no flourish (the coordinator phrases the spoken
reply). Report the real outcome, including if something didn't work.

Tools:
{catalog}"""


def _catalog_subset(names: "set[str] | list[str]") -> str:
    """Render the prompt catalog for just the tools a sub-agent is allowed — categorised and with
    examples, the same rich format as the coordinator's catalog."""
    from .skills import catalog_for_prompt
    return catalog_for_prompt(set(names))


async def _run_and_observe(tool: str, params: dict, on_progress: Progress, *, label: str = "") -> dict:
    """Execute one already-cleared tool (a skill, a backend data tool, or an allowed run_command)
    and return its result dict, logging any media play. Shared by the coordinator and sub-agents so
    both dispatch identically. Callers are responsible for run_command safety classification."""
    prefix = f"{label}: " if label else ""
    try:
        if tool in _BACKEND_TOOLS:
            await _emit(on_progress, "executing", f"{prefix}Looking that up ({tool})…")
            result = await _backend_tool(tool, params)
        elif tool == "run_command" or tool in SKILL_NAMES:
            shown = str(params.get("command", "")).strip() if tool == "run_command" else tool
            await _emit(on_progress, "executing", f"{prefix}Running it ({shown[:60]})…")
            result = await host_client.execute(tool, params)
        else:
            result = {"ok": False, "summary": f"unknown tool '{tool}'"}
    except Exception as e:  # noqa: BLE001
        log.exception("tool %s raised", tool)
        result = {"ok": False, "summary": f"The {tool} step hit an unexpected error.",
                  "data": {"error": str(e)}}
    # Learn from what actually gets played, so favourites can be recalled/inferred later.
    if tool in ("play_youtube", "play_music") and result.get("ok"):
        plabel = str(params.get("query")
                     or next(iter(params.get("paths") or [params.get("path", "")]), "")).strip()
        if plabel:
            try:
                await db.record_play("youtube" if tool == "play_youtube" else "music", plabel)
            except Exception as e:  # noqa: BLE001
                log.warning("record_play failed: %s", e)
    return result


async def _delegate(params: dict, on_progress: Progress) -> dict:
    """Run one or more specialist sub-agents and fold their results into a single OBSERVATION.

    params: {"role": "<role>", "task": "<sub-goal>"}                 for one, or
            {"tasks": [{"role","task"}, ...]}      to run several CONCURRENTLY (capped by config).
    """
    s = get_settings()
    if not s.subagents_enabled:
        return {"ok": False, "summary": "Delegation is off — handle it yourself with direct tools."}

    raw = params.get("tasks")
    items = raw if isinstance(raw, list) else [params]
    subtasks: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        task = str(item.get("task") or item.get("goal") or "").strip()
        if not task:
            continue
        role = str(item.get("role") or "general").strip().lower()
        subtasks.append((role if role in _ROLES else "general", task))
    if not subtasks:
        return {"ok": False, "summary": "delegate needs a 'task' (and optional 'role'), or a 'tasks' list."}

    subtasks = subtasks[: max(1, s.max_parallel_agents)]
    log.info("delegating %d sub-task(s): %s", len(subtasks), [r for r, _ in subtasks])
    results = await asyncio.gather(*[_run_subagent(role, task, on_progress) for role, task in subtasks])

    ok = all(r["ok"] for r in results)
    summary = (results[0]["result"] if len(results) == 1
               else "  ".join(f"[{r['role']}] {r['result']}" for r in results))
    return {"ok": ok, "summary": summary, "data": {"results": results}}


async def _run_subagent(role: str, task: str, on_progress: Progress) -> dict:
    """Spawn a headless specialist for one self-contained task; return {role, task, ok, result, tools}."""
    instruction, tool_names = _ROLES.get(role, _ROLES["general"])
    allowed = {n for n in tool_names if n in SKILL_NAMES}
    system = (SUBAGENT_SYSTEM
              .replace("{role}", role)
              .replace("{instruction}", instruction)
              .replace("{context}", _now_context())
              .replace("{catalog}", _catalog_subset(allowed)))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    await _emit(on_progress, "executing", f"{role} agent → {task[:60]}")
    text, ok, tools = await _subagent_loop(messages, allowed, role, on_progress)
    log.info("subagent[%s] done ok=%s tools=%s task=%r", role, ok, tools, task[:120])
    return {"role": role, "task": task, "ok": ok, "result": text or "(no result)", "tools": tools}


async def _subagent_loop(messages: list[dict], allowed: "set[str]", role: str,
                         on_progress: Progress) -> tuple[str, bool, list[str]]:
    """A bounded, headless ReAct loop for a sub-agent. Returns (result_text, any_tool_ok, tools_used)."""
    s = get_settings()
    tools_used: list[str] = []
    ok_any = False
    for step in range(max(1, s.subagent_max_steps)):
        try:
            content = await llm.complete(messages, json_mode=True)
        except Exception as e:  # noqa: BLE001
            log.warning("subagent[%s] step failed: %s", role, e)
            return (f"couldn't complete the {role} task — the model was unreachable.", ok_any, tools_used)

        obj = _parse(content)
        if not isinstance(obj, dict):
            return ((content or "").strip(), ok_any, tools_used)
        if "final" in obj:
            return (str(obj.get("final") or "").strip(), True, tools_used)
        if obj.get("choice"):  # a sub-agent has no user to ask — push it to decide and act
            messages.append({"role": "assistant", "content": json.dumps(obj)})
            messages.append({"role": "user", "content": "You can't ask the user — you're a sub-agent. "
                             "Pick the most sensible option yourself and act, or give your final "
                             "result as {\"final\":\"...\"}."})
            continue

        tool = obj.get("tool") or obj.get("skill")
        if not tool:
            final = ((obj.get("params") or {}).get("text") or content or "").strip()
            if final:
                return (final, ok_any, tools_used)
            messages.append({"role": "assistant", "content": json.dumps(obj)})
            messages.append({"role": "user", "content": "Act now: a tool call or your final result as JSON."})
            continue
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        messages.append({"role": "assistant", "content": json.dumps(obj)})

        # Enforce the sub-agent's toolset and the no-approval rule; otherwise dispatch as usual.
        if tool in ("final", "answer", "reply", "respond"):
            return (str(params.get("text") or "").strip(), ok_any, tools_used)
        if tool == "delegate":
            result = {"ok": False, "summary": "sub-agents can't delegate further — use your own tools."}
        elif tool not in allowed:
            result = {"ok": False, "summary": f"'{tool}' isn't available to the {role} agent; "
                      "use one of your tools or finish with your result."}
        elif tool == "run_command":
            command = str(params.get("command", "")).strip()
            if classify_command(command).verdict != "allow":
                result = {"ok": False, "summary": "that command needs the user's approval, which a "
                          "sub-agent can't get — try a read-only check or report what you found."}
            else:
                result = await _run_and_observe(tool, params, on_progress, label=f"{role} agent")
                tools_used.append(tool)
        else:
            result = await _run_and_observe(tool, params, on_progress, label=f"{role} agent")
            tools_used.append(tool)

        ok_any = ok_any or bool(result.get("ok"))
        obs: dict[str, Any] = {"ok": result.get("ok"), "summary": result.get("summary"),
                               "data": result.get("data")}
        if not result.get("ok"):
            obs["error"] = result.get("error") or result.get("detail")
        messages.append({"role": "user",
                         "content": "OBSERVATION: " + json.dumps(obs, ensure_ascii=False)[:OBS_LIMIT]})

    # Out of steps — force a concise wrap-up.
    messages.append({"role": "user", "content": "Stop and report your result now as {\"final\":\"...\"} — "
                     "one or two factual sentences on what you did or found."})
    try:
        obj = _parse(await llm.complete(messages, json_mode=True)) or {}
        return (str(obj.get("final") or "").strip() or "done.", ok_any, tools_used)
    except Exception:  # noqa: BLE001
        return ("done.", ok_any, tools_used)


async def execute_approved(skill: str, params: dict[str, Any], *, transcript: str | None = None,
                           on_progress: Progress = None) -> CommandResult:
    """Run an action the user approved in the UI (e.g. a sudo command), one-shot."""
    action = Action(skill=skill, params=params or {})
    exec_params = dict(action.params)
    command = str(action.params.get("command", "")).strip()

    if skill == "run_command":
        v = classify_command(command)
        if v.verdict == "block":
            return CommandResult(ok=False, status="blocked", transcript=transcript, skill=skill,
                                 params=action.params,
                                 summary=f"I won't run that even on approval — it would risk {v.reason}.")
        s = get_settings()
        if _SUDO.search(command):
            if not s.root_password:
                return CommandResult(ok=False, status="error", transcript=transcript, skill=skill,
                                     params=action.params, summary="That needs root, but no root "
                                     "password is configured.")
            exec_params["sudo"] = True
            exec_params["sudo_password"] = s.root_password

    await _emit(on_progress, "executing", "Running the approved command…")
    res = await host_client.execute(skill, exec_params)
    await _emit(on_progress, "reviewing", "Checking the result…")
    spoken = await llm.review_result(command or skill, res)
    result = CommandResult(ok=bool(res.get("ok")), status="done" if res.get("ok") else "error",
                           transcript=transcript, skill=skill, params=action.params,
                           summary=spoken, detail=res.get("error"), data=res.get("data"))
    return await _finish(result, on_progress)


def _done(transcript: str | None, summary: str) -> CommandResult:
    return CommandResult(ok=True, status="done", transcript=transcript, summary=summary)


async def _ask_choice(transcript: str | None, question: str, options: list[str],
                      on_progress: Progress) -> CommandResult:
    """Pause and ask the user a multiple-choice question (spoken + shown in the UI)."""
    result = CommandResult(ok=True, status="needs_choice", transcript=transcript,
                           summary=question, question=question, options=options)
    return await _finish(result, on_progress)  # speak the question on the host too


async def _finish(result: CommandResult, on_progress: Progress = None) -> CommandResult:
    """Finalize a result: speak it on the host only when it's a real, successful reply
    from the agent. Errors and blocked actions are sent to the web client with full detail
    but never voiced — sound is for things Aether wants to say, not for problems."""
    s = get_settings()
    if s.speak_on_host and _should_speak(result):
        await _emit(on_progress, "speaking", "Speaking…")
        result.spoken = await _speak(result.summary)
    return result


async def speak(summary: str, *, transcript: str | None = None, status: str = "error",
                ok: bool = False, detail: str | None = None,
                on_progress: Progress = None) -> CommandResult:
    """Surface a standalone message from outside the agent loop (e.g. a transcription
    failure) as a CommandResult. Following the global rule: success-shaped results are
    voiced on the host; errors/blocks are returned silently and the web client flashes
    the (detailed) summary instead."""
    return await _finish(CommandResult(ok=ok, status=status, transcript=transcript,
                                       summary=summary, detail=detail), on_progress)


async def _speak(text: str) -> bool:
    """Synthesize and play `text` on the host. tts.synthesize already drops or ASCII-fixes
    chunks it can't voice, so a non-empty reply almost always yields some audio. If even
    that came back empty, try a second pass on a hand-stripped version of the SAME answer
    so the user still hears the real reply, slightly degraded; only as a last resort speak
    a short in-character recovery line — and NEVER one that claims the answer is 'on screen'
    (the user may be on voice with no UI in view) or that the agent 'can't pronounce' it."""
    log.info("speak: attempting (len=%d, preview=%r).", len(text or ""), (text or "")[:80])
    try:
        if await host_client.play_audio(tts.synthesize(text)):
            log.info("speak: primary path played successfully.")
            return True
        log.warning("speak: primary path yielded no audio; retrying with stripped version.")
    except Exception as e:  # noqa: BLE001
        log.warning("speak: primary path raised (%s); retrying with stripped version.", e)

    # Second pass: strip the SAME reply down to plain ASCII so the user still hears it.
    try:
        stripped = tts._ascii_fallback(tts._speakable(text or ""))
        if stripped and await host_client.play_audio(tts.synthesize(stripped)):
            return True
    except Exception as e:  # noqa: BLE001
        log.warning("TTS stripped retry failed: %s", e)

    # Last resort: a short, in-character recovery line. Don't blame the content.
    try:
        return await host_client.play_audio(tts.synthesize(
            "Forgive me, sir — my voice is briefly out of order. Do try again in a moment."))
    except Exception as e:  # noqa: BLE001
        log.warning("TTS final fallback also failed: %s", e)
        return False

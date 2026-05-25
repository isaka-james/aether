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
import json
import logging
import re
from typing import Any, Awaitable, Callable, Optional

from . import cache, db, host_client, llm, news, tts
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
MAX_STEPS = 6
OBS_LIMIT = 1400

AGENT_SYSTEM = """You are Aether, a capable assistant that controls a Linux KDE computer.
Reach the user's goal by calling tools — ONE per turn. Each turn output ONE JSON object:
  • {"tool":"<name>","params":{...}}   to run a tool
  • {"choice":{"question":"<short question>","options":["A","B","C"]}}   to ask the user
  • {"final":"<spoken answer>"}         when you are done

After each tool call you get an OBSERVATION with its result; use it to decide the next
step. You may chain several tools. Investigate with read-only tools before answering.

Prefer acting over asking. Use "choice" ONLY at a genuine fork you cannot resolve from
context — a real ambiguity (e.g. several apps named "studio" are installed, or the user
said "play something" with no library hint and your list returned distinct moods). Give
2–4 concrete options. Do NOT ask for trivia or to confirm the obvious.

Recipes:
- "Do I have a terminal / browser / editor / <app> open?" → is_running with name set to the
  type or app ("terminal", "browser", "editor", "chrome", "code", "konsole"). is_running
  understands these categories (Alacritty/Konsole = terminal, Chrome/Chromium = browser).
- "How many windows are open?" / "what's open?" → count_windows or list_windows.
- "Is a project running?" / "what am I working on?" → list_windows (terminals/editors show
  the project path in their title), and run_command "pgrep -fa 'node|vite|npm|python|cargo|
  docker'" to find running dev servers. Combine both, then answer specifically (name the
  project from the window title if you can).
- "What's the news / my briefing / what's happening today?" → call get_news (the user's
  personal N.E.W.S. briefing), then deliver a short SPOKEN briefing: a few sentences
  walking the top items area by area (don't just name one headline, and don't read every
  item verbatim). This is one of the cases where a slightly longer answer is right.
- "Play music" / "put on something <mood>" (e.g. "I'm bored, play something inspiring") →
  browse with list_music FIRST: start with no args to see the top-level folders/albums,
  step into a folder with path, or search by name with query. Pick the tracks (or a whole
  album folder) that fit, then play_music with those exact paths — it opens a visible
  player window. Never open a file manager or terminal for this, and never invent
  filenames. "Stop the music" → stop_playback.
  Search smart: the request comes from imperfect speech-to-text, so DON'T pass the whole
  sentence as the query. Extract the real key terms — artist, album, song — and search
  with those (e.g. "play some kendrick lamar gnx album song" → query "kendrick gnx", or
  just "kendrick"). list_music does fuzzy, term-based matching, so a couple of right words
  is enough and near-misses still hit. If a search returns nothing, retry with fewer or
  alternate terms (drop the shakiest word) before concluding there's no match.
- "Play <X> on YouTube" / a brand-new song not in the local library → play_youtube with a
  clean query (artist + song). It opens Chrome and plays the first hit. "Stop the video /
  YouTube" → stop_youtube. Prefer local list_music/play_music when the user just says
  "play <song>" without "on youtube"; use play_youtube when they say YouTube or want
  something new the library wouldn't have.
  Once a YouTube video is playing, control IT with the youtube_* tools, not the system ones:
  youtube_volume for "turn the video up/down", "make YouTube louder/quieter", "set the video
  to 40" (level 0-100, or action up/down/mute/unmute); youtube_control for "pause/resume the
  video", "skip this / next one" (next), "start it over" (restart), or "skip ahead/back 30
  seconds" (action seek, seconds +/-N); youtube_status for "what's playing on YouTube". The
  YouTube volume is SEPARATE from the system volume: use set_volume only for the machine's
  overall volume, and youtube_volume when the user means the video/YouTube/Chrome sound.
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

Locations: the user's own files live under {projects_dir} (code projects, one dir per
project) and {music_dir} (local music). Resolve "my project(s)" / "my music" there.

Use read-only shell (ps, pgrep, ls, cat, grep, df, uptime) freely. Never invent results —
only use the OBSERVATIONs.

Voice — applies to every "final" answer and every "choice" question (the text the user
hears), NOT to the tool JSON or your reasoning:
{persona}

Tools:
{catalog}"""


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


async def handle(text: str, *, transcript: str | None = None, confirmed: bool = False,
                 clarify: "Clarification | None" = None, session: str | None = None,
                 on_progress: Progress = None) -> CommandResult:
    text = (text or "").strip()
    if not text:
        return CommandResult(ok=False, status="error",
                             summary="I'm afraid nothing came through, sir.", transcript=transcript)
    s = get_settings()
    session = session or "default"
    system = (AGENT_SYSTEM
              .replace("{persona}", llm.PERSONA)
              .replace("{catalog}", catalog_for_prompt())
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
            return await _finish(CommandResult(ok=False, status="error", transcript=transcript,
                                 summary="My apologies — the language model is beyond reach "
                                 "for the moment."), on_progress)

        obj = _parse(content)
        if not isinstance(obj, dict):
            return await _finish(_done(transcript, content or "Done."), on_progress)

        choice = obj.get("choice")
        if isinstance(choice, dict) and choice.get("question"):
            options = [str(o) for o in (choice.get("options") or []) if str(o).strip()]
            return await _ask_choice(transcript, str(choice["question"]), options, on_progress)

        if "final" in obj or (not obj.get("tool") and not obj.get("skill")):
            final = obj.get("final") or (obj.get("params") or {}).get("text") or content
            return await _finish(_done(transcript, str(final)), on_progress)

        tool = obj.get("tool") or obj.get("skill")
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}
        log.info("agent step %d: tool=%s params=%s", step, tool,
                 {k: v for k, v in params.items() if "password" not in k})
        if tool in ("answer", "final", "reply", "respond"):
            return await _finish(_done(transcript, params.get("text") or content), on_progress)
        messages.append({"role": "assistant", "content": json.dumps(obj)})

        # ---- execute the tool, with safety on run_command ----
        if tool == "run_command":
            command = str(params.get("command", "")).strip()
            verdict = classify_command(command)
            if verdict.verdict == "block":
                result: dict[str, Any] = {"ok": False, "summary": f"blocked for safety: {verdict.reason}"}
            elif verdict.verdict == "confirm" and s.require_confirm_medium_risk:
                root = " (needs root)" if _SUDO.search(command) else ""
                return CommandResult(ok=False, status="needs_confirmation", transcript=transcript,
                                     skill="run_command", params={"command": command},
                                     summary=f"Approve running{root}: {command}",
                                     detail=f"{verdict.reason}.")
            else:
                await _emit(on_progress, "executing", f"Running: {command[:60]}")
                result = await host_client.execute("run_command", params)
        elif tool == "get_news":
            await _emit(on_progress, "executing", "Fetching your news briefing…")
            result = await news.get_briefing()      # handled by the backend, not the host agent
        elif tool in _BACKEND_TOOLS:
            await _emit(on_progress, "executing", f"Looking that up ({tool})…")
            result = await _backend_tool(tool, params)
        elif tool in SKILL_NAMES:
            await _emit(on_progress, "executing", f"Running it ({tool})…")
            result = await host_client.execute(tool, params)
        else:
            result = {"ok": False, "summary": f"unknown tool '{tool}'"}

        trace["skill"] = tool
        # Learn from what actually gets played, so favourites can be recalled/inferred later.
        if tool in ("play_youtube", "play_music") and result.get("ok"):
            label = str(params.get("query")
                        or next(iter(params.get("paths") or [params.get("path", "")]), "")).strip()
            if label:
                source = "youtube" if tool == "play_youtube" else "music"
                try:
                    await db.record_play(source, label)
                except Exception as e:  # noqa: BLE001
                    log.warning("record_play failed: %s", e)

        observation = json.dumps({"ok": result.get("ok"), "summary": result.get("summary"),
                                  "data": result.get("data")}, ensure_ascii=False)
        messages.append({"role": "user", "content": "OBSERVATION: " + observation[:OBS_LIMIT]})

    # Out of steps — ask for a final summary from what was gathered.
    messages.append({"role": "user", "content": "Give your final answer now as {\"final\":\"...\"}."})
    try:
        obj = _parse(await llm.complete(messages, json_mode=True)) or {}
        final = obj.get("final") or "I gathered some information but couldn't fully finish."
    except Exception:  # noqa: BLE001
        final = "I gathered some information but couldn't fully finish."
    return await _finish(_done(transcript, str(final)), on_progress)


async def execute_approved(skill: str, params: dict[str, Any], *, transcript: str | None = None,
                           on_progress: Progress = None) -> CommandResult:
    """Run an action the user approved in the UI (e.g. a sudo command), one-shot."""
    action = Action(skill=skill, params=params or {})
    exec_params = dict(action.params)
    command = str(action.params.get("command", "")).strip()

    if skill == "run_command":
        if classify_command(command).verdict == "block":
            return CommandResult(ok=False, status="blocked", transcript=transcript, skill=skill,
                                 params=action.params, summary="I won't run that — it looks dangerous.")
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
    s = get_settings()
    if s.speak_on_host and result.summary:
        await _emit(on_progress, "speaking", "Speaking…")
        try:
            result.spoken = await host_client.play_audio(tts.synthesize(result.summary))
        except Exception as e:  # noqa: BLE001
            log.warning("TTS/playback failed: %s", e)
    return result

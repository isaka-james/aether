"""Tool dispatch — the leaf shared by the coordinator and the sub-agents.

Holds the backend (data-layer) tools, the single-tool executor `_run_and_observe`, and the tiny
helpers `_emit` (progress) and `_parse` (lenient JSON). Deliberately imports nothing from the
rest of the agent package, so both the coordinator loop and the sub-agent loop can dispatch a
tool identically without an import cycle.
"""
import json
import logging
from typing import Awaitable, Callable, Optional

from .. import db, host_client, timers
from ..skills import SKILL_NAMES

log = logging.getLogger("aether.agent.tools")

# An optional async on_progress(step, label) callback drives the web client's phase UI.
Progress = Optional[Callable[[str, str], Awaitable[None]]]

OBS_LIMIT = 4000  # generous enough that a long multi-item observation isn't chopped mid-JSON

# Tools answered inside the backend (data layer), not dispatched to the host agent. Checked
# before SKILL_NAMES so they don't get routed to the host even though they're in the catalog.
_BACKEND_TOOLS = {"list_favorites", "remember_favorite", "forget_favorite",
                  "get_preference", "set_preference", "play_history",
                  "set_timer", "list_timers", "cancel_timer"}

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
        removed = await db.remove_favorite(label, kind)
        return {"ok": bool(removed),
                "summary": f"Removed “{removed}” from favourites." if removed
                else f"I didn't find “{label}” in your favourites.",
                "data": {"removed": removed}}

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

    if tool == "set_timer":
        label = str(params.get("label") or params.get("message") or params.get("name") or "").strip()
        seconds = timers.duration_seconds(params)
        if seconds <= 0:
            return {"ok": False, "summary": "How long should it run? Tell me the minutes or seconds."}
        if seconds > timers.MAX_SECONDS:
            return {"ok": False, "summary": "That's longer than I can hold a timer for (24 hours max)."}
        timers.schedule(label, seconds)
        human = timers.humanize(seconds)
        summary = f"I'll remind you to {label} in {human}." if label else f"Timer set for {human}."
        return {"ok": True, "summary": summary, "data": {"label": label, "seconds": seconds}}

    if tool == "list_timers":
        active = timers.list_active()
        if not active:
            return {"ok": True, "summary": "No timers or reminders are running.", "data": {"timers": []}}
        parts = [f"{t['label']} ({timers.humanize(t['remaining_seconds'])} left)" if t["label"]
                 else f"{timers.humanize(t['remaining_seconds'])} left" for t in active]
        return {"ok": True, "summary": "Running: " + "; ".join(parts) + ".", "data": {"timers": active}}

    if tool == "cancel_timer":
        selector = str(params.get("label") or params.get("id") or params.get("name") or "").strip() or None
        cancelled = timers.cancel(selector)
        if not cancelled:
            return {"ok": False, "summary": "I didn't find a matching timer to cancel.",
                    "data": {"cancelled": []}}
        n = len(cancelled)
        summary = ("Cancelled all timers." if selector is None
                   else f"Cancelled {n} timer{'s' if n != 1 else ''}.")
        return {"ok": True, "summary": summary, "data": {"cancelled": cancelled}}

    return {"ok": False, "summary": f"unknown backend tool '{tool}'"}


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

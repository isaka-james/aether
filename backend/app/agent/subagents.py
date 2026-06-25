"""Multi-agent: the coordinator can hand a focused sub-goal to a specialist sub-agent via the
`delegate` tool, and run several in parallel.

Sub-agents are headless — no voice, no user questions, no approval-gated shell — so the
human-in-the-loop and safety guarantees stay with the coordinator (see loop.py).
"""
import asyncio
import json
import logging
from typing import Any

from .. import llm
from ..config import get_settings
from ..safety import classify_command
from ..skills import SKILL_NAMES, catalog_for_prompt
from .prompts import SUBAGENT_SYSTEM, _now_context
from .tools import OBS_LIMIT, Progress, _emit, _parse, _run_and_observe

log = logging.getLogger("aether.agent.subagents")

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
        ["weather", "news", "web_search", "notifications", "clear_notifications", "notify",
         "find_files", "find_tool", "capabilities", "system_info",
         "clipboard", "camera", "open_url", "run_command"],
    ),
    "general": (
        "You handle one focused sub-task with the full set of tools.",
        sorted(SKILL_NAMES),
    ),
}


def _catalog_subset(names: "set[str] | list[str]") -> str:
    """Render the prompt catalog for just the tools a sub-agent is allowed — categorised and with
    examples, the same rich format as the coordinator's catalog."""
    return catalog_for_prompt(set(names))


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

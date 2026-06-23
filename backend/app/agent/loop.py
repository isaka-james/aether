"""The coordinator: an agentic, multi-step loop.

For each request the model runs a ReAct-style loop: it calls a tool (skill), reads the
result, and decides the next step — chaining several read-only tools to investigate
before answering, or performing an action. It ends by emitting a final spoken answer.

  user ─▶ [LLM picks a tool] ─▶ safety screen ─▶ host agent ─▶ OBSERVATION ─▶ loop…
       └────────────────────────────── {"final": "..."} ──────────────────────────▶ speak

Safety:
  • Structured skills (bluetooth_power, list_windows, …) run automatically.
  • run_command is classified: block → voiced refusal (catastrophic, never run); allow → runs;
    confirm → the loop pauses and asks the user to approve (then execute_approved runs it).

An optional on_progress(step, label) async callback drives the web client's phase UI.
"""
import json
import logging
import re
from typing import Any

from .. import cache, db, host_client, llm, tts
from ..config import get_settings
from ..models import Action, Clarification, CommandResult
from ..safety import classify_command
from ..skills import catalog_for_prompt
from . import understand, verify
from .prompts import (AGENT_SYSTEM, _DELEGATION_PROMPT, _capabilities_note,
                      _machine_context, _now_context, _user_context)
from .state import AgentState, Phase, StopReason
from .subagents import _delegate
from .tools import OBS_LIMIT, Progress, _emit, _parse, _run_and_observe

log = logging.getLogger("aether.orchestrator")

_SUDO = re.compile(r"\bsudo\b")
# A capable cloud model (DeepSeek) drives the loop, so give it room to investigate state,
# resolve a conflict, act, and verify — a smart multi-step chain shouldn't get truncated.
MAX_STEPS = 9
MAX_VERIFY = 2  # at most this many verify→fix rounds before we finish with an honest answer
# Voiced when the model returns nothing usable — better a graceful line than silence.
FALLBACK_REPLY = "I'm afraid I've come up short on that one, sir."

# Tools that only READ state. A request that used only these (a pure question / investigation)
# hasn't changed anything, so it skips the verify pass — there's nothing to re-check.
READ_ONLY_TOOLS = frozenset({
    "list_music", "list_windows", "count_windows", "is_running", "running_apps", "list_projects",
    "list_input_devices", "now_playing", "youtube_status", "weather", "notifications",
    "system_info", "get_brightness", "bluetooth_status", "wifi_status", "capabilities",
    "find_tool", "find_files", "play_history", "list_favorites", "get_preference",
})

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


async def handle(text: str, *, transcript: str | None = None,
                 clarify: "Clarification | None" = None, session: str | None = None,
                 on_progress: Progress = None) -> CommandResult:
    text = (text or "").strip()
    if not text:
        return CommandResult(ok=False, status="error",
                             summary="I'm afraid nothing came through, sir.", transcript=transcript)
    s = get_settings()
    session = session or "default"
    context = await cache.get_context(session)

    # Understand phase: refine the raw (often messy speech-to-text) request into a precise goal +
    # checkable success criteria, resolving "it/that/again" from the recent context. On a genuine
    # fork it asks the user now rather than guessing. Skipped when resuming an answered choice
    # (clarify set) so we never re-ask what the user just decided.
    intent = None
    if clarify is None:
        intent = await understand.refine_request(text, context)
        if intent.ambiguous:
            return await _ask_choice(transcript, intent.question, intent.options, on_progress)

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
    # Inject the refined objective + success criteria as guidance the agent works to (and later verifies).
    obj_note = understand.objective_note(intent, text) if intent else ""
    if obj_note:
        messages.append({"role": "system", "content": obj_note})
    # Short follow-up memory: replay recent turns so "and now mute it" resolves in context.
    for turn in context:
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
    state = AgentState(goal=(intent.goal if intent else text),
                       success_criteria=(intent.success_criteria if intent else []))
    result = await _loop(messages, transcript, on_progress, trace=trace, state=state)
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
                *, trace: dict | None = None, state: "AgentState | None" = None) -> CommandResult:
    """The coordinator: discover/plan → execute → verify → stop. `state` carries the working
    memory (goal, success criteria, what's been done, repeated-call guard) across turns."""
    s = get_settings()
    trace = trace if trace is not None else {}
    state = state or AgentState()
    for step in range(MAX_STEPS):
        state.step = step
        await _emit(on_progress, "thinking", "Thinking…" if step == 0 else "Reasoning about the result…")
        try:
            content = await llm.complete(messages, json_mode=True)
        except Exception as e:  # noqa: BLE001
            log.warning("agent step failed: %s", e)
            state.stop_reason = StopReason.LLM_ERROR
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
            state.stop_reason = StopReason.NEEDS_USER
            return await _ask_choice(transcript, str(choice["question"]), options, on_progress)

        tool = obj.get("tool") or obj.get("skill")

        # ---- the model wants to finish: verify the goal before we accept it ----
        if "final" in obj:
            draft = (obj.get("final") or (obj.get("params") or {}).get("text") or "").strip() or FALLBACK_REPLY
            concluded = await _conclude(state, transcript, draft, messages, on_progress)
            if concluded is not None:
                return concluded
            continue  # verification sent us back to fix something

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
            draft = (params.get("text") or "").strip() or FALLBACK_REPLY
            concluded = await _conclude(state, transcript, draft, messages, on_progress)
            if concluded is not None:
                return concluded
            continue

        # ---- loop-protection: don't spin on the identical call ----
        sig = f"{tool}:{json.dumps(params, sort_keys=True, ensure_ascii=False)}"
        repeat = state.record_call(sig)
        if state.repeat_count >= 2:  # the same call three times running — stop rather than spin
            state.stop_reason = StopReason.UNRECOVERABLE
            log.info("loop-protection: stopping after a repeated identical call to %s", tool)
            messages.append({"role": "assistant", "content": json.dumps(obj)})
            messages.append({"role": "user", "content": "You've repeated the identical action with no "
                             "progress. Stop now and give your final answer as {\"final\":\"...\"}: state "
                             "what you achieved, or plainly what you could not."})
            try:
                fobj = _parse(await llm.complete(messages, json_mode=True)) or {}
                return await _finish(_done(transcript, str(fobj.get("final") or FALLBACK_REPLY)), on_progress)
            except Exception:  # noqa: BLE001
                return await _finish(_done(transcript, FALLBACK_REPLY), on_progress)
        if repeat:  # second identical call — nudge instead of running it again
            messages.append({"role": "assistant", "content": json.dumps(obj)})
            messages.append({"role": "user", "content": "You just ran exactly this call; it won't return "
                             "anything new. Change the arguments, try a different tool, or conclude."})
            continue

        state.phase = Phase.EXECUTE
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
                    state.stop_reason = StopReason.NEEDS_USER
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
        # A successful tool that isn't purely read-only means we've CHANGED something — record it,
        # so the verify pass runs before we finish (and so a pure question/lookup skips it).
        if result.get("ok") and tool not in READ_ONLY_TOOLS:
            state.acted = True
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
    state.stop_reason = StopReason.EXHAUSTED
    messages.append({"role": "user", "content": "Give your final answer now as {\"final\":\"...\"}."})
    try:
        obj = _parse(await llm.complete(messages, json_mode=True)) or {}
        final = obj.get("final") or "I gathered some information but couldn't fully finish."
    except Exception:  # noqa: BLE001
        final = "I gathered some information but couldn't fully finish."
    return await _finish(_done(transcript, str(final)), on_progress)


def _evidence(messages: list[dict]) -> str:
    """A compact transcript of what the agent actually did — its tool calls and the OBSERVATIONS —
    for the verifier to judge against (skips the large system prompt)."""
    parts: list[str] = []
    for m in messages:
        role, content = m.get("role"), m.get("content") or ""
        if role == "assistant":
            parts.append("ACTION: " + content[:300])
        elif role == "user" and content.startswith("OBSERVATION:"):
            parts.append(content[:600])
    return "\n".join(parts[-12:])[:3000]


async def _maybe_verify(state: AgentState, draft: str, messages: list[dict],
                        on_progress: Progress) -> str | None:
    """The verify gate. Returns the final text to finish with, or None to keep iterating (a fix
    instruction has been appended to `messages`). Only runs when the agent actually CHANGED
    something toward a goal with success criteria; otherwise it's a no-op pass-through."""
    s = get_settings()
    if not (s.verify_actions and state.success_criteria and state.acted):
        return draft
    if state.verify_attempts >= MAX_VERIFY:
        return draft  # verified/fixed enough times — finish with the honest draft
    state.phase = Phase.VERIFY
    await _emit(on_progress, "reviewing", "Verifying the result…")
    verdict = await verify.verify_goal(state.goal, state.success_criteria, _evidence(messages), draft)
    if verdict.met:
        return draft
    state.verify_attempts += 1
    messages.append({"role": "user", "content":
                     "VERIFICATION FAILED — do NOT claim the task is done. " + verdict.reason
                     + (f" Fix it: {verdict.fix_hint}." if verdict.fix_hint else "")
                     + " Take the action(s) needed to actually satisfy the goal, then finish."})
    state.phase = Phase.EXECUTE
    return None


async def _conclude(state: AgentState, transcript: str | None, draft: str, messages: list[dict],
                    on_progress: Progress) -> "CommandResult | None":
    """Run the verify gate and either finalize (return a CommandResult) or signal iterate (None)."""
    final_text = await _maybe_verify(state, draft, messages, on_progress)
    if final_text is None:
        return None
    state.phase = Phase.DONE
    state.stop_reason = StopReason.VERIFIED_DONE if state.acted else StopReason.ANSWERED
    return await _finish(_done(transcript, final_text), on_progress)


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

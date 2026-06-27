"""Understand — refine the raw request before the loop runs.

Speech-to-text is noisy and people speak in fragments ("turn it up", "play that again"). This
phase makes one small LLM call to turn the raw request into a precise, executable objective with
concrete, checkable success criteria (which the verify gate later tests), resolving references
like "it/that/again" from the recent conversation. If the request is genuinely ambiguous it
surfaces a question for the user; otherwise it just sharpens the goal. Everything degrades safely
to "use the raw text as the goal" so a refine failure never blocks a request.
"""
import logging
from dataclasses import dataclass, field

from .. import llm
from ..config import get_settings
from .prompts import REFINE_SYSTEM
from .tools import _parse

log = logging.getLogger("aether.agent.understand")


@dataclass
class Intent:
    goal: str                                   # one clear sentence: what to achieve
    refined_request: str                        # the cleaned-up request, in the user's voice
    success_criteria: list[str] = field(default_factory=list)   # observable "done" conditions
    plan: list[str] = field(default_factory=list)               # rough steps for a multi-step goal
    requires_action: bool = False               # True when fulfilling this CHANGES the machine's
                                                # state (must call a tool) vs. merely answering
    ambiguous: bool = False                     # True only when we truly can't proceed
    question: str = ""                          # if ambiguous: what to ask
    options: list[str] = field(default_factory=list)            # if ambiguous: 2-4 concrete choices


def _format_context(context: list[dict]) -> str:
    """Compact the last few follow-up turns into a short transcript for reference resolution."""
    lines = []
    for turn in (context or [])[-4:]:
        role = "You" if turn.get("role") == "assistant" else "User"
        content = (turn.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def refine_request(text: str, context: list[dict] | None = None) -> Intent:
    """Turn a raw request into an Intent. Never raises — degrades to the raw text as the goal."""
    raw = (text or "").strip()
    s = get_settings()
    if not s.refine_request or not raw:
        return Intent(goal=raw, refined_request=raw)

    messages = [{"role": "system", "content": REFINE_SYSTEM}]
    convo = _format_context(context)
    if convo:
        messages.append({"role": "user", "content": "Recent conversation (for resolving "
                         f"'it/that/again'):\n{convo}"})
    messages.append({"role": "user", "content": f"Raw request: {raw!r}\nReturn only the JSON."})

    try:
        out = await llm.complete(messages, json_mode=True, max_tokens=320, temperature=0.1)
        obj = _parse(out)
        if not isinstance(obj, dict):
            return Intent(goal=raw, refined_request=raw)
        goal = str(obj.get("goal") or "").strip() or raw
        refined = str(obj.get("refined_request") or "").strip() or raw
        criteria = [str(c).strip() for c in (obj.get("success_criteria") or []) if str(c).strip()]
        plan = [str(p).strip() for p in (obj.get("plan") or []) if str(p).strip()]
        options = [str(o).strip() for o in (obj.get("options") or []) if str(o).strip()]
        # Only treat as ambiguous when there's a real, answerable fork (a question + 2+ options).
        ambiguous = bool(obj.get("ambiguous")) and bool(str(obj.get("question") or "").strip()) and len(options) >= 2
        intent = Intent(goal=goal, refined_request=refined, success_criteria=criteria[:5],
                        plan=plan[:6], requires_action=bool(obj.get("requires_action")),
                        ambiguous=ambiguous, question=str(obj.get("question") or "").strip(),
                        options=options[:4])
        log.info("refined: goal=%r criteria=%d plan=%d action=%s ambiguous=%s", intent.goal[:120],
                 len(intent.success_criteria), len(intent.plan), intent.requires_action, intent.ambiguous)
        return intent
    except Exception as e:  # noqa: BLE001
        log.warning("refine_request failed (%s) — using raw text as goal.", e)
        return Intent(goal=raw, refined_request=raw)


def objective_note(intent: Intent, raw: str) -> str:
    """A compact system note injecting the refined goal + plan + success criteria into the loop, or
    "" if the refinement added nothing over the raw request."""
    has_plan = len(intent.plan) >= 2   # a single-step "plan" is noise, not a plan
    if (not intent.success_criteria and not has_plan
            and intent.goal.strip().lower() == raw.strip().lower()):
        return ""
    note = f"Refined objective for this request: {intent.goal}"
    if has_plan:
        # Only inject a plan for genuinely multi-step work — a single-step request needs no ceremony.
        note += "\nA workable plan (adapt it as observations come in; don't follow it blindly): " + \
                " → ".join(intent.plan)
    if intent.success_criteria:
        note += "\nSuccess criteria (the request is only done when ALL hold): " + \
                "; ".join(f"({i+1}) {c}" for i, c in enumerate(intent.success_criteria))
        note += "\nWork to meet them, then verify they actually hold before you give your final answer."
    return note

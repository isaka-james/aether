"""Verify — did the agent actually achieve the goal?

After the agent has acted and is ready to finish, this makes one small LLM call that judges the
drafted reply against the goal's success criteria using ONLY the evidence (the tool actions and
their observations). If a criterion clearly isn't met, the loop sends the agent back to fix it
instead of letting it claim success. Degrades safely to "accept" on any failure, so verification
can never wedge a request.
"""
import json
import logging
from dataclasses import dataclass

from .. import llm
from ..config import get_settings
from .prompts import VERIFY_SYSTEM
from .tools import _parse

log = logging.getLogger("aether.agent.verify")


@dataclass
class Verdict:
    met: bool
    reason: str = ""
    fix_hint: str = ""


async def verify_goal(goal: str, criteria: list[str], evidence: str, draft_reply: str,
                      plan: list[str] | None = None) -> Verdict:
    """Judge whether the goal was actually achieved given `evidence` — criteria met, planned steps
    carried out, and the reply honest and complete. Never raises — defaults to met=True."""
    s = get_settings()
    if not s.verify_actions or not criteria:
        return Verdict(True)
    ctx = {"goal": goal, "plan": plan or [], "success_criteria": criteria,
           "evidence": evidence, "draft_reply": draft_reply}
    try:
        out = await llm.complete(
            [{"role": "system", "content": VERIFY_SYSTEM},
             {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}],
            json_mode=True, max_tokens=220, temperature=0.0)
        obj = _parse(out)
        if not isinstance(obj, dict):
            return Verdict(True)
        met = bool(obj.get("met", True))
        verdict = Verdict(met, str(obj.get("reason") or "").strip(), str(obj.get("fix_hint") or "").strip())
        log.info("verify: met=%s reason=%r", verdict.met, verdict.reason[:160])
        return verdict
    except Exception as e:  # noqa: BLE001
        log.warning("verify_goal failed (%s) — accepting the result.", e)
        return Verdict(True)

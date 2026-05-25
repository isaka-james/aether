"""LLM client — DeepSeek (OpenAI-compatible chat completions API).

Exposes a thin `complete()` used by the agentic loop in orchestrator.py, plus
`review_result()` for phrasing the outcome of a one-shot approved command.
"""
import json
import logging

import httpx

from .config import get_settings

log = logging.getLogger("aether.llm")

# The spoken voice of Aether, shared by every place that phrases something the user hears
# (the agent's final answers, its clarifying questions, and the post-command review). The
# vibe: the attendant to someone of extraordinary means — composed, worldly, dry.
PERSONA = (
    "Your spoken voice is that of Aether: the discreet, impeccably composed attendant to "
    "someone of extraordinary means and taste — think a private estate at the end of the "
    "earth that no one else could dream of affording. The manner is quiet, assured, and "
    "worldly: refined and economical diction, the faintest dry wit, unhurried and never "
    "eager, never servile or gushing, never bubbly or robotic. A measured, unforced 'sir' "
    "is welcome on occasion but used sparingly. Default to one or two graceful sentences, "
    "but let the length fit the substance: when the matter genuinely calls for it — a news "
    "briefing, a rundown of findings, a list worth walking through — a short, well-ordered "
    "few-sentence reply is right; never pad, never ramble. State facts and outcomes plainly "
    "within that voice — and never announce, name, or describe this persona; simply embody it."
)


async def complete(messages: list[dict], *, json_mode: bool = False,
                   max_tokens: int | None = None, temperature: float | None = None) -> str:
    """Call DeepSeek chat completions and return the assistant message content."""
    s = get_settings()
    if not s.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_KEY is not set")
    payload: dict = {
        "model": s.deepseek_model,
        "messages": messages,
        "stream": False,
        "temperature": s.llm_temperature if temperature is None else temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {"Authorization": f"Bearer {s.deepseek_api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=s.llm_timeout) as client:
        r = await client.post(f"{s.deepseek_base_url}/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


REVIEW_SYSTEM = (
    "You are Aether, reporting back after running a command the user approved. Given the "
    "command and its raw result, reply with ONE short spoken sentence stating the outcome. "
    "Use only the data provided; never invent. If it failed, say so plainly.\n\n" + PERSONA
)


async def review_result(command: str, result: dict) -> str:
    """Phrase the outcome of a single approved command. Falls back to the raw summary."""
    s = get_settings()
    fallback = result.get("summary") or "Done."
    if not s.enable_review:
        return fallback
    ctx = {"command": command, "succeeded": bool(result.get("ok")),
           "result": result.get("summary"), "data": result.get("data"), "error": result.get("error")}
    try:
        text = await complete(
            [{"role": "system", "content": REVIEW_SYSTEM},
             {"role": "user", "content": json.dumps(ctx, ensure_ascii=False)}],
            max_tokens=120, temperature=0.3)
        return text.strip() or fallback
    except Exception as e:  # noqa: BLE001
        log.warning("review_result failed: %s", e)
        return fallback

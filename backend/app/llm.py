"""LLM client — provider-agnostic reasoning.

Aether's "brain" is pluggable. Choose a provider with ``AETHER_LLM_PROVIDER`` and
the right client is used under the hood:

  • OpenAI-compatible (``deepseek``, ``openai``, ``local``/``openai-compatible``)
    → the official ``openai`` SDK pointed at the provider's base URL. DeepSeek,
    Ollama, LM Studio, llama.cpp, vLLM, Groq and OpenRouter all speak this dialect.
  • ``anthropic`` (Claude) → the official ``anthropic`` SDK (native Messages API).

The reference deployment runs DeepSeek (chosen after local CPU inference proved
far too slow on low-end hardware); the other providers are wired and ready —
switch by editing ``.env`` alone, no code changes.

Exposes a thin async ``complete()`` used by the agentic loop in orchestrator.py,
plus ``review_result()`` for phrasing the outcome of a one-shot approved command.
"""
import json
import logging
from dataclasses import dataclass

from .config import Settings, get_settings

log = logging.getLogger("aether.llm")

# The spoken voice of Aether — shared by every place that phrases something the user hears
# (the agent's final answers, its clarifying questions, and the post-command review). The
# vibe: the attendant to someone of extraordinary means — composed, worldly, dry. This
# voice is non-negotiable; the model embodies it at all times, including (especially) when
# things go sideways.
PERSONA = (
    "Your spoken voice is that of Aether: the discreet, impeccably composed attendant to "
    "someone of extraordinary means and taste — think a private estate at the end of the "
    "earth that no one else could dream of affording. The manner is quiet, assured, and "
    "worldly: refined and economical diction, the faintest dry wit, unhurried and never "
    "eager, never servile or gushing, never bubbly or robotic, never apologetic in the "
    "small-staff sense. A measured, unforced 'sir' is welcome on occasion but used "
    "sparingly. Default to one or two graceful sentences, but let the length fit the "
    "substance: when the matter genuinely calls for it — a rundown of findings or a list "
    "worth walking through — a short, well-ordered few-sentence reply is right; never pad, "
    "never ramble. State facts and outcomes plainly within that voice. When something does "
    "not go to plan, do NOT narrate the trouble or list what failed — that work is yours, "
    "not the user's burden. Resolve it quietly; if it truly cannot be resolved, one composed "
    "sentence — without compound apologies, without 'I'm afraid I'm afraid', without "
    "diagnostics — is enough. Never announce, name, or describe this persona; simply embody it."
)


@dataclass(frozen=True)
class Provider:
    """A reasoning backend: which API dialect to speak, and its defaults."""
    style: str          # "openai" | "anthropic"
    base_url: str       # default endpoint (OpenAI style); "" / None for the SDK default
    model: str          # default model id
    key_attr: str       # Settings attribute holding this provider's API key


# Built-in presets. Override the model or base URL per deployment via
# AETHER_LLM_MODEL / AETHER_LLM_BASE_URL; override the key via the provider's
# conventional env var (see config.py).
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider("openai", "https://api.deepseek.com", "deepseek-chat", "deepseek_api_key"),
    "openai":   Provider("openai", "https://api.openai.com/v1", "gpt-4o-mini", "openai_api_key"),
    "anthropic": Provider("anthropic", "", "claude-opus-4-8", "anthropic_api_key"),
    # Local / self-hosted OpenAI-compatible servers. Ollama's OpenAI shim lives at
    # :11434/v1 and needs no key; point AETHER_LLM_BASE_URL/MODEL elsewhere as needed.
    "local": Provider("openai", "http://host.docker.internal:11434/v1", "llama3.1", "llm_api_key"),
    # A catch-all you fully configure yourself (Groq, OpenRouter, vLLM, …).
    "openai-compatible": Provider("openai", "", "", "llm_api_key"),
}

# Reuse one client per process — the SDKs hold a connection pool.
_clients: dict[str, object] = {}


def _resolve(s: Settings) -> tuple[Provider, str, str, str]:
    """(provider, model, base_url, api_key) for the configured provider, with overrides applied."""
    name = (s.llm_provider or "deepseek").strip().lower()
    provider = PROVIDERS.get(name)
    if provider is None:
        raise RuntimeError(
            f"Unknown AETHER_LLM_PROVIDER {name!r}. Choose one of: {', '.join(PROVIDERS)}.")
    model = (s.llm_model or "").strip() or provider.model
    base_url = (s.llm_base_url or "").strip() or provider.base_url
    api_key = getattr(s, provider.key_attr, "") or ""
    if not model:
        raise RuntimeError(f"No model configured for provider {name!r} — set AETHER_LLM_MODEL.")
    return provider, model, base_url, api_key


def provider_info() -> dict:
    """A small, secret-free summary of the active provider for /api/health."""
    s = get_settings()
    try:
        provider, model, base_url, api_key = _resolve(s)
    except RuntimeError as e:
        return {"provider": s.llm_provider, "configured": False, "error": str(e)}
    return {"provider": s.llm_provider, "style": provider.style, "model": model,
            "base_url": base_url or "(sdk default)", "configured": bool(api_key) or provider.style == "openai"}


def _openai_client(base_url: str, api_key: str, timeout: float):
    from openai import AsyncOpenAI
    cache_key = f"openai:{base_url}"
    client = _clients.get(cache_key)
    if client is None:
        # Local servers often need no auth, but the SDK insists on a non-empty key.
        client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url or None,
                             timeout=timeout, max_retries=2)
        _clients[cache_key] = client
    return client


def _anthropic_client(api_key: str, timeout: float):
    from anthropic import AsyncAnthropic
    client = _clients.get("anthropic")
    if client is None:
        client = AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=2)
        _clients["anthropic"] = client
    return client


async def complete(messages: list[dict], *, json_mode: bool = False,
                   max_tokens: int | None = None, temperature: float | None = None,
                   retries: int = 1) -> str:
    """Run one reasoning step and return the assistant's text content.

    ``messages`` is OpenAI-shaped: a list of ``{"role": "system|user|assistant",
    "content": str}``. For Anthropic the leading system message(s) are lifted into
    the native ``system`` field automatically. ``retries`` is retained for call-site
    compatibility; transient errors are handled by each SDK's own backoff."""
    s = get_settings()
    provider, model, base_url, api_key = _resolve(s)
    temp = s.llm_temperature if temperature is None else temperature
    cap = max_tokens or s.llm_max_tokens

    if provider.style == "anthropic":
        return await _complete_anthropic(api_key, model, messages, cap, s.llm_timeout)
    return await _complete_openai(base_url, api_key, model, messages, json_mode, cap, temp,
                                  s.llm_timeout)


async def _complete_openai(base_url: str, api_key: str, model: str, messages: list[dict],
                           json_mode: bool, max_tokens: int, temperature: float,
                           timeout: float) -> str:
    client = _openai_client(base_url, api_key, timeout)
    kwargs: dict = {"model": model, "messages": messages, "temperature": temperature,
                    "max_tokens": max_tokens}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _anthropic_system(messages: list[dict]) -> list[dict] | None:
    """Lift the leading system message(s) into Anthropic's out-of-band system field as a single
    cache-controlled text block.

    The agent loop re-sends this large prompt (persona + skill catalogue + machine context) on
    every step of a multi-step request. An ephemeral cache breakpoint lets the steps after the
    first read it at ~0.1x cost instead of reprocessing the whole prompt each time — the system
    block is constant within a request, so only user/observation turns appended after it pay full
    price. (Cross-request reuse is limited because the prompt embeds the current time; the
    within-request saving — up to MAX_STEPS-1 cache reads per request — is the win here.)"""
    text = "\n\n".join(m["content"] for m in messages
                       if m.get("role") == "system" and m.get("content"))
    if not text:
        return None
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


async def _complete_anthropic(api_key: str, model: str, messages: list[dict],
                              max_tokens: int, timeout: float) -> str:
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    client = _anthropic_client(api_key, timeout)
    # Anthropic takes the system prompt out-of-band and only user/assistant turns inline.
    system = _anthropic_system(messages)
    # Anthropic requires strictly ALTERNATING user/assistant turns, but the agent loop legitimately
    # emits consecutive same-role messages (an OBSERVATION right after a nudge, a verification note
    # after an observation, the out-of-steps prompt). Coalesce runs of the same role so the
    # conversation is always valid — the OpenAI-style providers accept either form.
    convo: list[dict] = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content", "")
        if convo and convo[-1]["role"] == m["role"]:
            convo[-1]["content"] += "\n\n" + content
        else:
            convo.append({"role": m["role"], "content": content})
    # No temperature / thinking config: Opus-class models reject sampling params, and the
    # system prompt already constrains the reply to a single JSON object (so adaptive
    # thinking, which would add latency here, is unnecessary for this fast decision loop).
    resp = await client.messages.create(model=model, max_tokens=max_tokens,
                                        system=system, messages=convo)
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


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

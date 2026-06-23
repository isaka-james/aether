"""Runtime configuration, loaded from environment variables / .env.

Everything is overridable via the environment. Backend-only settings use the
``AETHER_`` prefix (e.g. ``AETHER_LLM_PROVIDER``); a few well-known third-party
keys are read under their conventional names (``DEEPSEEK_API_KEY``,
``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, ``ROOT_PWD``) so you can paste them in
the way every other tool expects.
"""
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_", env_file=".env", extra="ignore")

    # --- Auth (single user) ---
    username: str = "admin"
    password: str = "changeme"  # plaintext here; hashed in memory at startup
    jwt_secret: str = "please-change-this-long-random-secret"
    jwt_expire_minutes: int = 60 * 24 * 7  # one week

    # --- Reasoning LLM (provider-agnostic) ---------------------------------
    # Aether's "brain" is pluggable. Pick a provider; the rest is inferred from
    # sensible per-provider defaults that you can still override.
    #   deepseek           -> DeepSeek cloud (OpenAI-compatible) — the default
    #   openai             -> OpenAI cloud
    #   anthropic          -> Anthropic Claude (native Messages API)
    #   local | openai-compatible -> any OpenAI-compatible server (Ollama,
    #                        LM Studio, llama.cpp, vLLM, Groq, OpenRouter, …)
    llm_provider: str = "deepseek"
    # Override the provider's default model / base URL when you need to. Empty
    # means "use the provider preset" (see llm.PROVIDERS).
    llm_model: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.1     # ignored by providers that don't accept it (e.g. Claude Opus)
    llm_timeout: float = 60.0
    llm_max_tokens: int = 1024       # cap on a single reasoning step's output

    # Per-provider API keys, read under their conventional names so they drop in
    # straight from each provider's dashboard. A request only needs the key for
    # the provider you actually selected; local servers usually need none.
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    # Generic fallback key for a custom/local OpenAI-compatible endpoint.
    llm_api_key: str = ""            # AETHER_LLM_API_KEY

    # --- Who Aether is assisting (personalises greetings & grounding) ------
    # Open-source friendly: nothing here is hard-coded to one machine. Fill these
    # in (or leave blank) — they only colour how the assistant addresses you and
    # where it assumes "here" is.
    user_name: str = ""              # AETHER_USER_NAME, e.g. "Alex"
    user_city: str = ""              # AETHER_USER_CITY, e.g. "Nairobi"
    user_country: str = ""           # AETHER_USER_COUNTRY, e.g. "Kenya"

    # --- User content locations (the host paths the agent's skills operate on) ---
    # Generic defaults; override to point at wherever your files actually live.
    # Kept in sync with the host agent's AETHER_MUSIC_DIR / AETHER_PROJECTS_DIR.
    music_dir: str = "~/Music"
    projects_dir: str = "~/Projects"
    # IANA timezone (e.g. "Europe/Berlin") used to ground the model's sense of the
    # current date/time. Empty -> the backend's local zone. Reads AETHER_TZ or plain TZ.
    timezone: str = Field(default="", validation_alias=AliasChoices("AETHER_TZ", "TZ"))

    # --- Host agent (runs natively on the host: executes commands, plays audio) ---
    host_agent_url: str = "http://host.docker.internal:8474"
    host_agent_token: str = "please-change-this-shared-secret"
    host_agent_timeout: float = 120.0

    # --- Speech-to-text ---
    # Strategy: "auto" tries the keyless online endpoint first (fast, good with proper
    # nouns) and falls back to local whisper; "google" is online-only; "local" is
    # whisper-only. With auto/google the audio is sent to Google's public speech API.
    stt_provider: str = "auto"           # auto | google | local
    stt_online_timeout: float = 8.0      # seconds before giving up on the online endpoint
    # Local fallback (faster-whisper). "small.en" is far better at spelling than "base"
    # and still tolerable on this CPU; ".en" models are English-only.
    whisper_model: str = "small.en"      # tiny | base | small | small.en | medium ...
    whisper_compute_type: str = "int8"   # int8 is fastest on CPU
    whisper_language: str = "en"
    whisper_prompt: str = ""             # optional vocabulary hint to bias spelling

    # --- Text-to-speech (Kokoro) ---
    kokoro_onnx_path: str = "/models/kokoro/kokoro-v1.0.onnx"
    kokoro_voices_path: str = "/models/kokoro/voices-v1.0.bin"
    kokoro_voice: str = "af_sky"
    kokoro_speed: float = 1.0
    kokoro_lang: str = "en-us"

    # --- Privileged execution ---
    # The host root password (env var ROOT_PWD, not AETHER_-prefixed). When a command
    # needs root, it is sent to the web client for approval; on approval the host agent
    # runs it with sudo using this password. Empty -> root commands can't be approved.
    root_password: str = Field(default="", validation_alias="ROOT_PWD")

    # --- Persistence (optional; the app degrades gracefully if these are unreachable) ---
    # Postgres holds history/transcripts, the notification archive, favourites & preferences.
    # Redis holds short follow-up context, caches, and the live notification pub/sub channel.
    database_url: str = ""       # e.g. postgresql://aether:aether@db:5432/aether
    redis_url: str = ""          # e.g. redis://redis:6379/0
    context_ttl: int = 600       # seconds of follow-up conversation memory kept in Redis
    context_turns: int = 6       # how many recent turns to replay into the model
    notify_poll_interval: float = 12.0  # how often the backend pulls new host notifications

    # --- Behavior ---
    speak_on_host: bool = True   # play TTS through the host speakers
    require_confirm_medium_risk: bool = True
    enable_review: bool = True   # second AI pass: review the result and report the real outcome

    # --- Multi-agent (coordinator + delegated sub-agents) ------------------
    # The main loop can delegate a focused sub-goal to a specialist sub-agent — its own role,
    # tighter prompt, and a subset of the tools — and run several in parallel. Sub-agents are
    # headless: they never speak, ask the user, or run approval-gated/sudo commands; the
    # coordinator keeps the voice and any approvals. Turn this off to force the classic single agent.
    subagents_enabled: bool = True
    subagent_max_steps: int = 6      # tool-steps a sub-agent may take before it must conclude
    max_parallel_agents: int = 3     # cap on sub-agents one delegate call runs concurrently


@lru_cache
def get_settings() -> Settings:
    return Settings()

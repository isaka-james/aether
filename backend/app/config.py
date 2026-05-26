"""Runtime configuration, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AETHER_", env_file=".env", extra="ignore")

    # --- Auth ---
    username: str = "admin"
    password: str = "changeme"  # plaintext here; hashed in memory at startup
    jwt_secret: str = "please-change-this-long-random-secret"
    jwt_expire_minutes: int = 60 * 24 * 7  # one week

    # --- LLM: DeepSeek (OpenAI-compatible cloud API) ---
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_KEY")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"       # V3 (fast); use "deepseek-reasoner" for R1
    llm_temperature: float = 0.1
    llm_timeout: float = 60.0

    # --- User content locations (the host paths the agent's skills operate on) ---
    # Kept in sync with the host agent's AETHER_MUSIC_DIR / AETHER_PROJECTS_DIR; used to
    # tell the model where the user's files live.
    music_dir: str = "~/Music"
    projects_dir: str = "~/Projects"
    # IANA timezone (e.g. "Africa/Dar_es_Salaam") used to ground the model's sense of the
    # current date/time. Empty -> the backend's local zone. Reads AETHER_TZ or plain TZ.
    timezone: str = Field(default="", validation_alias=AliasChoices("AETHER_TZ", "TZ"))

    # --- Host agent (runs natively on the host: executes commands, plays audio) ---
    host_agent_url: str = "http://host.docker.internal:8765"
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

    # --- N.E.W.S. integration (personal news briefing service) ---
    # Aether reaches the n.e.w.s nginx on the host via the docker host-gateway.
    news_url: str = "http://host.docker.internal:4291/api"
    news_email: str = ""       # AETHER_NEWS_EMAIL — your n.e.w.s account
    news_password: str = ""    # AETHER_NEWS_PASSWORD

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
    news_cache_ttl: int = 300    # seconds to cache the news briefing
    notify_poll_interval: float = 12.0  # how often the backend pulls new host notifications

    # --- Behavior ---
    speak_on_host: bool = True   # play TTS through the host speakers
    require_confirm_medium_risk: bool = True
    enable_review: bool = True   # second AI pass: review the result and report the real outcome


@lru_cache
def get_settings() -> Settings:
    return Settings()

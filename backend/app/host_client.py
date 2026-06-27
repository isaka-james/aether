"""Client for the host agent — the native process that executes commands and plays
audio on the host machine. All calls are authenticated with a shared token.
"""
import logging
from typing import Any

import httpx

from .config import get_settings

log = logging.getLogger("aether.host")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """One pooled client for the whole process. The agent loop calls the host agent many times
    per request (a tool dispatch, an observation, a spoken reply); reusing the connection pool
    avoids a fresh TCP+TLS setup on every call — the LLM SDK clients in llm.py are shared for the
    same reason. Rebuilt if it was ever closed (e.g. across a lifespan restart in tests)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=get_settings().host_agent_timeout)
    return _client


async def close() -> None:
    """Close the pooled client on shutdown (wired into the FastAPI lifespan)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _headers() -> dict[str, str]:
    return {"X-Aether-Token": get_settings().host_agent_token}


async def execute(skill: str, params: dict[str, Any]) -> dict[str, Any]:
    """Ask the host agent to run a skill. Returns {ok, summary, data, error}."""
    s = get_settings()
    try:
        r = await _get_client().post(
            f"{s.host_agent_url}/execute",
            json={"skill": skill, "params": params},
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        return {"ok": False, "summary": "The host agent rejected the request.",
                "error": f"{e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:  # noqa: BLE001
        log.warning("host agent execute failed: %s", e)
        return {"ok": False, "summary": "I couldn't reach the host agent.", "error": str(e)}


async def play_audio(wav_bytes: bytes, *, flush: bool = False) -> bool:
    """Stream WAV bytes to the host agent to play on the host speakers. `flush` tells the host to
    cancel any audio still queued/playing first — set on the first clip of a new reply so a fresh
    answer supersedes a stale one. Returns True once the host has accepted the clip (it then plays
    in order on the host's serialized player)."""
    if not wav_bytes:
        return False
    s = get_settings()
    headers = {**_headers(), "Content-Type": "audio/wav"}
    if flush:
        headers["X-Aether-Flush"] = "1"
    try:
        r = await _get_client().post(f"{s.host_agent_url}/play", content=wav_bytes, headers=headers)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("host agent play failed: %s", e)
        return False

"""Redis: the fast, disposable layer.

Three jobs, all optional (no REDIS_URL or an unreachable server → every call is a safe
no-op and the assistant still works):
  • follow-up context — a short, TTL'd history of recent turns per session so the model can
    resolve "and now mute it" / "turn that down" against what just happened
  • caching — cache slow lookups (the news briefing) for a few minutes
  • pub/sub — a channel the backend uses to fan host notifications out to web clients live
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from .config import get_settings

log = logging.getLogger("aether.cache")

try:
    import redis.asyncio as aioredis
except Exception:  # noqa: BLE001
    aioredis = None  # type: ignore

_redis: "aioredis.Redis | None" = None

NOTIFICATIONS_CHANNEL = "aether:notifications"


async def connect() -> bool:
    global _redis
    s = get_settings()
    if not s.redis_url or aioredis is None:
        log.info("Redis disabled (no REDIS_URL or redis lib) — context/cache/fan-out off.")
        return False
    try:
        client = aioredis.from_url(s.redis_url, decode_responses=True,
                                   socket_connect_timeout=5, socket_timeout=5)
        await client.ping()
        _redis = client
        log.info("Redis connected.")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Redis unavailable (%s) — continuing without it.", e)
        _redis = None
        return False


async def close() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        finally:
            _redis = None


def enabled() -> bool:
    return _redis is not None


# --- follow-up conversation context ------------------------------------------
def _ctx_key(session: str) -> str:
    return f"aether:ctx:{session or 'default'}"


async def get_context(session: str) -> list[dict]:
    """Recent turns (oldest→newest) as [{role, content}, …], or [] if disabled/empty."""
    if _redis is None:
        return []
    s = get_settings()
    try:
        raw = await _redis.lrange(_ctx_key(session), -2 * s.context_turns, -1)
        return [json.loads(x) for x in raw]
    except Exception as e:  # noqa: BLE001
        log.warning("get_context failed: %s", e)
        return []


async def push_turn(session: str, user_text: str, assistant_text: str) -> None:
    """Append a (user, assistant) exchange to the session's rolling context with a TTL."""
    if _redis is None or not (user_text or assistant_text):
        return
    s = get_settings()
    key = _ctx_key(session)
    try:
        pipe = _redis.pipeline()
        pipe.rpush(key, json.dumps({"role": "user", "content": user_text or ""}))
        pipe.rpush(key, json.dumps({"role": "assistant", "content": assistant_text or ""}))
        pipe.ltrim(key, -2 * s.context_turns, -1)
        pipe.expire(key, s.context_ttl)
        await pipe.execute()
    except Exception as e:  # noqa: BLE001
        log.warning("push_turn failed: %s", e)


async def clear_context(session: str) -> None:
    if _redis is None:
        return
    try:
        await _redis.delete(_ctx_key(session))
    except Exception as e:  # noqa: BLE001
        log.warning("clear_context failed: %s", e)


# --- generic caching ----------------------------------------------------------
async def cache_get(key: str) -> Any | None:
    if _redis is None:
        return None
    try:
        raw = await _redis.get(f"aether:cache:{key}")
        return json.loads(raw) if raw is not None else None
    except Exception as e:  # noqa: BLE001
        log.warning("cache_get failed: %s", e)
        return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    if _redis is None:
        return
    try:
        await _redis.set(f"aether:cache:{key}", json.dumps(value), ex=max(1, ttl))
    except Exception as e:  # noqa: BLE001
        log.warning("cache_set failed: %s", e)


# --- pub/sub fan-out ----------------------------------------------------------
async def publish(channel: str, message: dict) -> None:
    if _redis is None:
        return
    try:
        await _redis.publish(channel, json.dumps(message))
    except Exception as e:  # noqa: BLE001
        log.warning("publish failed: %s", e)


async def subscribe(channel: str, handler: Callable[[dict], Awaitable[None]]) -> None:
    """Loop forever, invoking `handler` for each message. Exits quietly if Redis is off."""
    if _redis is None:
        return
    try:
        pubsub = _redis.pubsub()
        await pubsub.subscribe(channel)
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                await handler(json.loads(msg["data"]))
            except Exception as e:  # noqa: BLE001
                log.warning("pubsub handler failed: %s", e)
    except Exception as e:  # noqa: BLE001
        log.warning("subscribe loop ended: %s", e)

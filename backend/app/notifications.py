"""Notification bridge: pull host notifications, archive them, fan them out live.

The host agent records KDE notifications as they fire (see host-agent/notify_recorder.py),
but the backend (in Docker) can't watch the host's session bus directly. So we poll the
agent's ``notifications`` skill on an interval, using a ``since`` cursor to fetch only new
ones, then:
  • archive each into Postgres (so "what did I miss yesterday?" works), and
  • publish them on the Redis channel — a subscriber broadcasts them to web clients live.

If Redis is off we broadcast straight to the in-process hub instead, so live fan-out still
works on a single backend. All best-effort; failures are logged and retried next tick.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from . import cache, db, host_client
from .config import get_settings

log = logging.getLogger("aether.notifications")

Broadcast = Callable[[dict], Awaitable[None]]


async def _deliver(rec: dict, broadcast: Broadcast) -> None:
    msg = {"type": "notification", "app": rec.get("app"),
           "summary": rec.get("summary"), "body": rec.get("body"), "ts": rec.get("ts")}
    if cache.enabled():
        await cache.publish(cache.NOTIFICATIONS_CHANNEL, msg)  # subscriber → hub
    else:
        await broadcast(msg)                                   # no Redis → straight to clients


async def poll_loop(broadcast: Broadcast) -> None:
    """Periodically pull new host notifications; archive + fan them out. Runs until cancelled."""
    s = get_settings()
    cursor: float = 0.0
    # Prime the cursor so we don't replay the whole backlog on first tick.
    try:
        first = await host_client.execute("notifications", {"since": 0, "limit": 100})
        cursor = float((first.get("data") or {}).get("latest_ts") or 0.0)
        await db.archive_notifications((first.get("data") or {}).get("items") or [])
    except Exception as e:  # noqa: BLE001
        log.debug("notification prime skipped: %s", e)

    while True:
        await asyncio.sleep(max(3.0, s.notify_poll_interval))
        try:
            res = await host_client.execute("notifications", {"since": cursor, "limit": 50})
            items = (res.get("data") or {}).get("items") or []
            if not items:
                continue
            await db.archive_notifications(items)
            for rec in items:
                await _deliver(rec, broadcast)
            cursor = max(cursor, float((res.get("data") or {}).get("latest_ts") or cursor))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.debug("notification poll tick failed: %s", e)


async def subscriber(broadcast: Broadcast) -> None:
    """Relay notifications published on Redis to connected web clients. No-op without Redis."""
    async def handler(msg: dict) -> None:
        await broadcast(msg)
    await cache.subscribe(cache.NOTIFICATIONS_CHANNEL, handler)

"""Timers & reminders.

In-process, best-effort timers. The agent schedules one with ``schedule()``; when it elapses
the backend announces it three ways, mirroring how host notifications already reach the user:
  • speaks it on the host speakers (when speak_on_host),
  • raises a host desktop notification (the `notify` skill), and
  • fans it out to web clients as a {"type": "notification", ...} message — Redis-published
    when available, otherwise straight to the in-process hub (the broadcaster main.py registers).

State is in memory only: a backend restart forgets pending timers. That's the right trade for a
"remind me in 10 minutes" feature — durable, survives-reboot reminders would need the DB and
startup rescheduling, which is a larger change.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import cache, host_client, tts
from .config import get_settings

log = logging.getLogger("aether.timers")

Broadcast = Callable[[dict], Awaitable[None]]
_broadcast: Broadcast | None = None

# Sanity bound so a mis-heard "set a timer for 90000" can't pin a task for a year.
MAX_SECONDS = 24 * 3600


@dataclass
class _Timer:
    id: str
    label: str          # "" for a bare timer; otherwise what to remind about
    seconds: float      # original duration (for the "your N-minute timer is up" wording)
    fire_at: float      # wall-clock epoch when it elapses (for remaining-time reporting)
    task: asyncio.Task


_timers: dict[str, _Timer] = {}


def set_broadcaster(fn: Broadcast | None) -> None:
    """Register the web fan-out callback (main.py wires this to the notification hub)."""
    global _broadcast
    _broadcast = fn


def humanize(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if sec and not h:  # drop seconds once we're into hours — it just clutters speech
        parts.append(f"{sec} second{'s' if sec != 1 else ''}")
    return " ".join(parts) or "0 seconds"


def duration_seconds(params: dict) -> float:
    """Sum the seconds/minutes/hours fields into a duration. Tolerant of missing/garbage values."""
    total = 0.0
    for key, mult in (("seconds", 1), ("minutes", 60), ("hours", 3600)):
        val = params.get(key)
        if val is None:
            continue
        try:
            total += float(val) * mult
        except (TypeError, ValueError):
            continue
    return total


async def _announce(label: str, seconds: float) -> None:
    """Deliver an elapsed timer: speak it, raise a desktop notification, fan out to web clients."""
    if label:
        spoken, summary = f"It's time — {label}.", f"Reminder: {label}"
    else:
        spoken = f"Your {humanize(seconds)} timer is up, sir."
        summary = f"Timer finished ({humanize(seconds)})"

    s = get_settings()
    if s.speak_on_host:
        try:
            wav = await asyncio.to_thread(tts.synthesize, spoken)
            if wav:
                await host_client.play_audio(wav)
        except Exception as e:  # noqa: BLE001 - a failed announcement must not crash the loop
            log.warning("timer speak failed: %s", e)
    try:
        await host_client.execute("notify", {"message": summary})
    except Exception as e:  # noqa: BLE001
        log.debug("timer desktop notify failed: %s", e)

    msg = {"type": "notification", "app": "Aether", "summary": summary,
           "body": spoken, "ts": time.time()}
    try:
        if cache.enabled():
            await cache.publish(cache.NOTIFICATIONS_CHANNEL, msg)
        elif _broadcast is not None:
            await _broadcast(msg)
    except Exception as e:  # noqa: BLE001
        log.warning("timer fan-out failed: %s", e)


def schedule(label: str, seconds: float) -> _Timer:
    """Start a timer that fires after `seconds`. Must be called from within the event loop."""
    tid = uuid.uuid4().hex[:8]

    async def _run() -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        _timers.pop(tid, None)
        await _announce(label, seconds)

    timer = _Timer(id=tid, label=label, seconds=seconds,
                   fire_at=time.time() + seconds, task=asyncio.create_task(_run()))
    _timers[tid] = timer
    log.info("timer %s scheduled: %.0fs label=%r", tid, seconds, label)
    return timer


def list_active() -> list[dict]:
    """Active timers, soonest first: [{id, label, remaining_seconds}]."""
    now = time.time()
    out = [{"id": t.id, "label": t.label, "remaining_seconds": max(0.0, t.fire_at - now)}
           for t in _timers.values()]
    return sorted(out, key=lambda d: d["remaining_seconds"])


def cancel(selector: str | None) -> list[dict]:
    """Cancel timers matching `selector` (an id or a label substring); cancel all if None/blank.
    Returns the cancelled timers' {id, label}."""
    sel = (selector or "").strip().lower()
    if not sel:
        victims = list(_timers.values())
    else:
        victims = [t for t in _timers.values()
                   if t.id == sel or (t.label and sel in t.label.lower())]
    for t in victims:
        t.task.cancel()
        _timers.pop(t.id, None)
    return [{"id": t.id, "label": t.label} for t in victims]


async def cancel_all() -> None:
    """Cancel every pending timer and await their teardown (used on shutdown)."""
    tasks = [t.task for t in _timers.values()]
    for task in tasks:
        task.cancel()
    _timers.clear()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

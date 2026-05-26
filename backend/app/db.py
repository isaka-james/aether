"""Postgres persistence (asyncpg).

Holds the things that make Aether feel like it *knows* the user across sessions:
  • interactions  — an audit log + conversation transcript of every command and outcome
  • notifications — an archive of host notifications captured by the agent's recorder
  • favorites     — saved/favourite songs, videos, etc. the user can recall by name
  • preferences   — remembered settings (favourite volume, default player, …)
  • play_history  — everything played, so favourites can be inferred from what's played most

Everything here is best-effort: if no DATABASE_URL is set or the database is unreachable,
the pool stays ``None`` and every function degrades to a safe no-op/empty result so the
core assistant keeps working. Never let persistence take the app down.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from .config import get_settings

log = logging.getLogger("aether.db")

try:
    import asyncpg
except Exception:  # noqa: BLE001 - dependency may be absent in minimal installs
    asyncpg = None  # type: ignore

_pool: "asyncpg.Pool | None" = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id          BIGSERIAL PRIMARY KEY,
    session     TEXT,
    transcript  TEXT,
    request     TEXT,
    skill       TEXT,
    status      TEXT,
    ok          BOOLEAN,
    summary     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS interactions_created_idx ON interactions (created_at DESC);

CREATE TABLE IF NOT EXISTS notifications (
    id          BIGSERIAL PRIMARY KEY,
    ts          DOUBLE PRECISION,
    app         TEXT,
    summary     TEXT,
    body        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ts, summary)
);
CREATE INDEX IF NOT EXISTS notifications_created_idx ON notifications (created_at DESC);

CREATE TABLE IF NOT EXISTS favorites (
    id              BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,
    label           TEXT NOT NULL,
    value           TEXT,
    play_count      INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_played_at  TIMESTAMPTZ,
    UNIQUE (kind, label)
);

CREATE TABLE IF NOT EXISTS preferences (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS play_history (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT,
    label       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS play_history_label_idx ON play_history (label);
"""


async def _init_conn(conn) -> None:
    # Let us pass/receive Python objects for JSONB columns.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads,
                              schema="pg_catalog")


async def connect() -> bool:
    """Create the pool and ensure the schema. Returns True if the DB is usable."""
    global _pool
    s = get_settings()
    if not s.database_url or asyncpg is None:
        log.info("Postgres disabled (no DATABASE_URL or asyncpg) — history/favourites off.")
        return False
    try:
        _pool = await asyncpg.create_pool(s.database_url, min_size=1, max_size=5,
                                          init=_init_conn, command_timeout=10)
        async with _pool.acquire() as conn:
            await conn.execute(_SCHEMA)
        log.info("Postgres connected; schema ready.")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Postgres unavailable (%s) — continuing without it.", e)
        _pool = None
        return False


async def close() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        finally:
            _pool = None


def enabled() -> bool:
    return _pool is not None


# --- interactions / transcripts ----------------------------------------------
async def log_interaction(*, session: str | None, transcript: str | None, request: str | None,
                          skill: str | None, status: str | None, ok: bool | None,
                          summary: str | None) -> None:
    if _pool is None:
        return
    try:
        async with _pool.acquire() as c:
            await c.execute(
                "INSERT INTO interactions (session, transcript, request, skill, status, ok, summary)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7)",
                session, transcript, request, skill, status, ok, summary)
    except Exception as e:  # noqa: BLE001
        log.warning("log_interaction failed: %s", e)


# --- notification archive -----------------------------------------------------
async def archive_notifications(items: list[dict]) -> int:
    """Upsert host-captured notifications, ignoring ones already stored. Returns the number
    of rows submitted (executemany doesn't report how many conflicts were skipped)."""
    if _pool is None or not items:
        return 0
    rows = [(float(i.get("ts") or 0), i.get("app"), i.get("summary"), i.get("body"))
            for i in items]
    try:
        async with _pool.acquire() as c:
            await c.executemany(
                "INSERT INTO notifications (ts, app, summary, body) VALUES ($1,$2,$3,$4)"
                " ON CONFLICT (ts, summary) DO NOTHING", rows)
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.warning("archive_notifications failed: %s", e)
        return 0


async def recent_notifications(limit: int = 20) -> list[dict]:
    if _pool is None:
        return []
    try:
        async with _pool.acquire() as c:
            rows = await c.fetch(
                "SELECT app, summary, body, created_at FROM notifications"
                " ORDER BY created_at DESC LIMIT $1", limit)
        return [{"app": r["app"], "summary": r["summary"], "body": r["body"],
                 "at": r["created_at"].isoformat()} for r in rows]
    except Exception as e:  # noqa: BLE001
        log.warning("recent_notifications failed: %s", e)
        return []


# --- favourites ---------------------------------------------------------------
async def add_favorite(kind: str, label: str, value: str | None = None) -> bool:
    if _pool is None:
        return False
    try:
        async with _pool.acquire() as c:
            await c.execute(
                "INSERT INTO favorites (kind, label, value) VALUES ($1,$2,$3)"
                " ON CONFLICT (kind, label) DO UPDATE SET value = COALESCE(EXCLUDED.value, favorites.value)",
                kind.lower(), label, value)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("add_favorite failed: %s", e)
        return False


async def remove_favorite(label: str, kind: str | None = None) -> int:
    if _pool is None:
        return 0
    try:
        async with _pool.acquire() as c:
            if kind:
                res = await c.execute("DELETE FROM favorites WHERE kind=$1 AND label ILIKE $2",
                                      kind.lower(), label)
            else:
                res = await c.execute("DELETE FROM favorites WHERE label ILIKE $1", label)
        # res like "DELETE <n>"
        return int(res.split()[-1]) if res else 0
    except Exception as e:  # noqa: BLE001
        log.warning("remove_favorite failed: %s", e)
        return 0


async def list_favorites(kind: str | None = None, limit: int = 25) -> list[dict]:
    if _pool is None:
        return []
    try:
        async with _pool.acquire() as c:
            if kind:
                rows = await c.fetch(
                    "SELECT kind, label, value, play_count FROM favorites WHERE kind=$1"
                    " ORDER BY play_count DESC, last_played_at DESC NULLS LAST, label LIMIT $2",
                    kind.lower(), limit)
            else:
                rows = await c.fetch(
                    "SELECT kind, label, value, play_count FROM favorites"
                    " ORDER BY play_count DESC, last_played_at DESC NULLS LAST, label LIMIT $1",
                    limit)
        return [{"kind": r["kind"], "label": r["label"], "value": r["value"],
                 "play_count": r["play_count"]} for r in rows]
    except Exception as e:  # noqa: BLE001
        log.warning("list_favorites failed: %s", e)
        return []


# --- play history -------------------------------------------------------------
async def record_play(source: str, label: str) -> None:
    """Log a play and bump its favourite counter if it's a saved favourite."""
    if _pool is None or not label:
        return
    try:
        async with _pool.acquire() as c:
            await c.execute("INSERT INTO play_history (source, label) VALUES ($1,$2)",
                            source, label)
            await c.execute(
                "UPDATE favorites SET play_count = play_count + 1, last_played_at = now()"
                " WHERE label ILIKE $1", label)
    except Exception as e:  # noqa: BLE001
        log.warning("record_play failed: %s", e)


async def top_plays(limit: int = 5, source: str | None = None) -> list[dict]:
    if _pool is None:
        return []
    try:
        async with _pool.acquire() as c:
            if source:
                rows = await c.fetch(
                    "SELECT label, COUNT(*) n FROM play_history WHERE source=$1"
                    " GROUP BY label ORDER BY n DESC LIMIT $2", source, limit)
            else:
                rows = await c.fetch(
                    "SELECT label, COUNT(*) n FROM play_history"
                    " GROUP BY label ORDER BY n DESC LIMIT $1", limit)
        return [{"label": r["label"], "count": r["n"]} for r in rows]
    except Exception as e:  # noqa: BLE001
        log.warning("top_plays failed: %s", e)
        return []


# --- request suggestions ------------------------------------------------------
async def top_requests(limit: int = 6, session: str | None = None) -> list[str]:
    """The user's most-frequently-issued requests (for the web's suggestion chips).

    Groups the interaction log by the request text (case-insensitively), favouring ones
    asked often and recently, and returns a representative phrasing of each. Trivial/short
    or failed entries are skipped so the chips stay useful."""
    if _pool is None:
        return []
    try:
        async with _pool.acquire() as c:
            rows = await c.fetch(
                """
                SELECT (array_agg(request ORDER BY created_at DESC))[1] AS sample,
                       COUNT(*) AS n, MAX(created_at) AS last
                FROM interactions
                WHERE request IS NOT NULL AND char_length(trim(request)) >= 6
                  AND ($1::text IS NULL OR session = $1)
                  AND COALESCE(ok, true) = true
                GROUP BY lower(trim(request))
                ORDER BY n DESC, last DESC
                LIMIT $2
                """,
                session, limit)
        return [r["sample"].strip() for r in rows if (r["sample"] or "").strip()]
    except Exception as e:  # noqa: BLE001
        log.warning("top_requests failed: %s", e)
        return []


# --- preferences --------------------------------------------------------------
async def get_preference(key: str, default: Any = None) -> Any:
    if _pool is None:
        return default
    try:
        async with _pool.acquire() as c:
            row = await c.fetchrow("SELECT value FROM preferences WHERE key=$1", key.lower())
        return row["value"] if row else default
    except Exception as e:  # noqa: BLE001
        log.warning("get_preference failed: %s", e)
        return default


async def set_preference(key: str, value: Any) -> bool:
    if _pool is None:
        return False
    try:
        async with _pool.acquire() as c:
            await c.execute(
                "INSERT INTO preferences (key, value) VALUES ($1,$2)"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                key.lower(), value)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("set_preference failed: %s", e)
        return False

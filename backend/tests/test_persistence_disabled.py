"""Tests for the graceful-degradation contract of the optional persistence layers.

With no DATABASE_URL / REDIS_URL configured (the conftest default), ``connect()`` is never
called, so the pool/client stay None. Every db/cache call must then be a safe no-op or empty
result — never an exception — so the assistant keeps working with no external services. These
run the async functions directly via asyncio.run (no event-loop plugin needed).
"""
import asyncio

from app import cache, db


def run(coro):
    return asyncio.run(coro)


def test_db_reports_disabled():
    assert db.enabled() is False


def test_db_reads_return_empty():
    assert run(db.top_requests()) == []
    assert run(db.recent_requests()) == []
    assert run(db.list_favorites()) == []
    assert run(db.top_plays()) == []
    assert run(db.recent_notifications()) == []
    assert run(db.get_preference("volume")) is None


def test_db_writes_are_noops_not_errors():
    # None/False returns, no raised exception, no connection attempt.
    assert run(db.log_interaction(session="s", transcript="t", request="r", skill="x",
                                  status="done", ok=True, summary="ok")) is None
    assert run(db.add_favorite("music", "Song")) is False
    assert run(db.set_preference("volume", 30)) is False
    assert run(db.remove_favorite("Song")) == 0
    assert run(db.record_play("music", "Song")) is None
    assert run(db.archive_notifications([{"ts": 1.0, "summary": "x"}])) == 0


def test_cache_reports_disabled():
    assert cache.enabled() is False


def test_cache_context_is_empty_and_writes_noop():
    assert run(cache.get_context("session")) == []
    assert run(cache.push_turn("session", "hi", "there")) is None
    assert run(cache.clear_context("session")) is None


def test_cache_generic_get_set_noop():
    assert run(cache.cache_get("key")) is None
    assert run(cache.cache_set("key", {"a": 1}, ttl=60)) is None


def test_cache_publish_noop():
    assert run(cache.publish(cache.NOTIFICATIONS_CHANNEL, {"hello": "world"})) is None

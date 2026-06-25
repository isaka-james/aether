"""Tests for the in-process timer/reminder scheduler and its agent tools.

Persistence and the host agent stay off (conftest), so nothing here touches a socket — the
firing path is exercised with a stubbed announcer / broadcaster.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app import timers
from app.agent.tools import _backend_tool


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_registry():
    timers._timers.clear()
    timers.set_broadcaster(None)
    yield
    timers._timers.clear()


def test_humanize_reads_naturally():
    assert timers.humanize(0) == "0 seconds"
    assert timers.humanize(1) == "1 second"
    assert timers.humanize(90) == "1 minute 30 seconds"
    assert timers.humanize(600) == "10 minutes"
    assert timers.humanize(3600) == "1 hour"
    assert timers.humanize(3660) == "1 hour 1 minute"  # seconds dropped once we're into hours


def test_duration_seconds_sums_and_tolerates_garbage():
    assert timers.duration_seconds({"minutes": 5}) == 300
    assert timers.duration_seconds({"hours": 1, "minutes": 30}) == 5400
    assert timers.duration_seconds({"seconds": "45"}) == 45
    assert timers.duration_seconds({"minutes": "oops"}) == 0
    assert timers.duration_seconds({}) == 0


def test_schedule_list_and_cancel():
    async def go():
        timers.schedule("tea", 1000)
        timers.schedule("", 2000)
        active = timers.list_active()
        assert len(active) == 2
        assert active[0]["label"] == "tea"                 # soonest first
        assert 0 < active[0]["remaining_seconds"] <= 1000
        assert [c["label"] for c in timers.cancel("tea")] == ["tea"]  # cancel by label
        assert len(timers.list_active()) == 1
        assert len(timers.cancel(None)) == 1               # cancel all
        assert timers.list_active() == []
    run(go())


def test_timer_fires_then_clears(monkeypatch):
    fired = []

    async def fake_announce(label, seconds):
        fired.append((label, seconds))

    monkeypatch.setattr(timers, "_announce", fake_announce)

    async def go():
        timers.schedule("ping", 0.02)
        assert len(timers.list_active()) == 1
        await asyncio.sleep(0.06)

    run(go())
    assert fired == [("ping", 0.02)]
    assert timers.list_active() == []  # popped after firing


def test_announce_fans_out_to_web_without_redis(monkeypatch):
    seen = []

    async def broadcaster(msg):
        seen.append(msg)

    async def noop(*a, **k):
        return None

    timers.set_broadcaster(broadcaster)
    monkeypatch.setattr(timers, "get_settings", lambda: SimpleNamespace(speak_on_host=False))
    monkeypatch.setattr(timers.host_client, "execute", noop)
    monkeypatch.setattr(timers.cache, "enabled", lambda: False)

    run(timers._announce("call mum", 600))
    assert seen and seen[0]["type"] == "notification"
    assert "call mum" in seen[0]["summary"]


def test_set_timer_tool_validates_schedules_and_cancels():
    async def go():
        assert (await _backend_tool("set_timer", {"label": "x"}))["ok"] is False      # no duration
        assert (await _backend_tool("set_timer", {"hours": 48}))["ok"] is False        # over the cap
        good = await _backend_tool("set_timer", {"label": "pasta", "minutes": 10})
        assert good["ok"] is True
        assert "pasta" in good["summary"] and "10 minutes" in good["summary"]
        listed = await _backend_tool("list_timers", {})
        assert listed["data"]["timers"][0]["label"] == "pasta"
        assert (await _backend_tool("cancel_timer", {"label": "pasta"}))["ok"] is True
        assert (await _backend_tool("list_timers", {}))["data"]["timers"] == []
        assert (await _backend_tool("cancel_timer", {"label": "ghost"}))["ok"] is False
    run(go())

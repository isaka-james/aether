"""The orchestrator entry points must never let an exception escape as a 500 — any unexpected
crash becomes a graceful error CommandResult instead. (The agent's per-step recovery handles the
common failures inside the loop; this is the outer safety net.)"""
import asyncio

from app.agent import loop


def run(coro):
    return asyncio.run(coro)


def test_handle_turns_an_unexpected_crash_into_a_graceful_error(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(loop, "_handle", boom)
    res = run(loop.handle("do something", transcript="do something"))
    assert res.ok is False and res.status == "error"   # graceful, not a raised exception
    assert "kaboom" in (res.detail or "")              # detail carries the cause for the web flash
    assert res.transcript == "do something"


def test_execute_approved_turns_a_crash_into_a_graceful_error(monkeypatch):
    async def boom(*a, **k):
        raise ValueError("nope")

    monkeypatch.setattr(loop, "_execute_approved", boom)
    res = run(loop.execute_approved("run_command", {"command": "ls"}, transcript="t"))
    assert res.ok is False and res.status == "error"
    assert "nope" in (res.detail or "")

"""The agent must never report "done" for something it didn't actually do — the bug where it
said a task was complete while nothing had happened and the speakers stayed silent.

These drive the real loop with a scripted model (llm.complete) and a stubbed host, asserting:
- a request that REQUIRES an action is refused when the model claims done without calling a tool,
  and only accepted once a tool has actually run and returned ok;
- a pure question still answers in a single step (the gate adds no cost to Q&A);
- a bare "done" with no JSON behind it is never accepted as success.
"""
import asyncio

import app.agent.loop as loop
from app.agent import understand
from app.agent.understand import Intent


def run(coro):
    return asyncio.run(coro)


def _no_real_speakers(monkeypatch):
    async def fake_speak(text):
        return True
    monkeypatch.setattr(loop, "_speak", fake_speak)


def _refine(monkeypatch, **fields):
    async def fake_refine(text, context=None):
        return Intent(goal=fields.get("goal", text), refined_request=text,
                      success_criteria=fields.get("success_criteria", []),
                      requires_action=fields.get("requires_action", False))
    monkeypatch.setattr(understand, "refine_request", fake_refine)


def _script(monkeypatch, responses):
    """Make llm.complete return each scripted string in turn; count the calls."""
    it = iter(responses)
    calls = {"n": 0}

    async def fake_complete(messages, **kw):
        calls["n"] += 1
        return next(it)

    monkeypatch.setattr(loop.llm, "complete", fake_complete)
    return calls


def test_done_with_no_tool_is_refused_until_the_action_runs(monkeypatch):
    _refine(monkeypatch, goal="lock the screen", requires_action=True)
    _no_real_speakers(monkeypatch)
    calls = _script(monkeypatch, [
        '{"final": "Done, the screen is locked."}',   # fabricated — no tool ran: must be refused
        '{"tool": "lock_screen", "params": {}}',      # the model actually acts
        '{"final": "The screen is locked, sir."}',    # now legitimate
    ])

    async def fake_execute(skill, params):
        assert skill == "lock_screen"
        return {"ok": True, "summary": "Locking the screen."}

    monkeypatch.setattr(loop.host_client, "execute", fake_execute)

    res = run(loop.handle("lock it", transcript="lock it", session="t"))
    assert res.status == "done"
    assert "locked" in (res.summary or "").lower()
    assert calls["n"] == 3            # the hollow claim was bounced back, the model had to act


def test_pure_question_answers_in_one_step(monkeypatch):
    _refine(monkeypatch, requires_action=False)
    _no_real_speakers(monkeypatch)
    calls = _script(monkeypatch, ['{"final": "The capital of France is Paris."}'])

    res = run(loop.handle("what's the capital of france", transcript="q", session="t"))
    assert res.status == "done"
    assert "Paris" in (res.summary or "")
    assert calls["n"] == 1            # no pushback, no extra round-trips for a plain question


def test_bare_done_text_is_not_accepted_as_success(monkeypatch):
    _refine(monkeypatch, requires_action=False)
    _no_real_speakers(monkeypatch)
    calls = _script(monkeypatch, ["done", '{"final": "Here is the actual answer."}'])

    res = run(loop.handle("tell me something", transcript="x", session="t"))
    assert res.status == "done"
    assert res.summary == "Here is the actual answer."
    assert calls["n"] == 2            # the hollow "done" was rejected and a real reply requested


def test_answer_is_spoken_sentence_by_sentence_while_it_streams(monkeypatch):
    """The voice should LEAD the text: each sentence of a plain answer is played as it streams from
    the model (first clip flushing stale audio), and the reply is not then spoken a second time."""
    _refine(monkeypatch, requires_action=False)

    async def streaming_complete(messages, json_mode=False, on_token=None, **kw):
        content = '{"final": "First sentence. Second sentence."}'
        if on_token:
            for ch in content:                 # emit char-by-char, like a real token stream
                await on_token(ch)
        return content

    monkeypatch.setattr(loop.llm, "complete", streaming_complete)

    async def fake_render(text):               # stand in for Kokoro
        return b"WAV:" + text.encode()
    monkeypatch.setattr(loop, "_render", fake_render)

    plays = []

    async def fake_play(wav, *, flush=False):
        plays.append(flush)
        return True
    monkeypatch.setattr(loop.host_client, "play_audio", fake_play)

    whole = {"called": False}

    async def fake_speak_whole(text):
        whole["called"] = True
        return True
    monkeypatch.setattr(loop, "_speak", fake_speak_whole)

    res = run(loop.handle("say two sentences", transcript="x", session="t"))
    assert res.status == "done" and res.spoken is True
    assert plays == [True, False]              # two sentences; the first clip flushes stale audio
    assert whole["called"] is False           # already voiced live — never re-spoken

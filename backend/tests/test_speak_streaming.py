"""Tests for the streaming speak path — per-sentence playback with look-ahead synthesis.

Synthesis and host playback are stubbed, so this checks ordering/robustness, not real audio:
playback must stay in order (never overlap), a chunk that fails to synthesise is skipped, and
the call reports whether anything actually played.
"""
import asyncio

from app.agent import loop


def run(coro):
    return asyncio.run(coro)


def test_stream_chunks_plays_every_chunk_in_order(monkeypatch):
    synthesised, played = [], []

    def fake_synth(chunk):
        synthesised.append(chunk)
        return f"wav:{chunk}".encode()

    async def fake_play(wav):
        played.append(wav.decode())
        return True

    monkeypatch.setattr(loop.tts, "synthesize_chunk", fake_synth)
    monkeypatch.setattr(loop.host_client, "play_audio", fake_play)

    assert run(loop._stream_chunks(["one.", "two.", "three."])) is True
    assert played == ["wav:one.", "wav:two.", "wav:three."]   # strict, non-overlapping order
    assert synthesised == ["one.", "two.", "three."]          # each synthesised exactly once


def test_stream_chunks_skips_chunks_that_fail_to_synthesise(monkeypatch):
    played = []

    def fake_synth(chunk):
        return b"" if chunk == "bad." else f"wav:{chunk}".encode()

    async def fake_play(wav):
        played.append(wav.decode())
        return True

    monkeypatch.setattr(loop.tts, "synthesize_chunk", fake_synth)
    monkeypatch.setattr(loop.host_client, "play_audio", fake_play)

    assert run(loop._stream_chunks(["good.", "bad.", "end."])) is True
    assert played == ["wav:good.", "wav:end."]   # the unvoiceable chunk is dropped, others play


def test_stream_chunks_returns_false_when_nothing_plays(monkeypatch):
    async def fake_play(wav):
        return True

    monkeypatch.setattr(loop.tts, "synthesize_chunk", lambda c: b"")
    monkeypatch.setattr(loop.host_client, "play_audio", fake_play)

    assert run(loop._stream_chunks(["a.", "b."])) is False

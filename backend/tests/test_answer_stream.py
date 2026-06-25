"""Tests for _FinalStreamer — the incremental extractor that surfaces the agent's final-answer
text out of the JSON it streams, so the web can show it live. This is the fragile piece of the
live-streaming feature, so it's exercised hard: arbitrary delta boundaries, escapes, and the
tool-call case (no final → stream nothing)."""
from app.agent.loop import _FinalStreamer


def feed_all(deltas):
    s = _FinalStreamer()
    return "".join(s.feed(d) for d in deltas)


def test_extracts_final_value_streamed_character_by_character():
    doc = '{"thought": "checking", "final": "Good evening, sir."}'
    assert feed_all(list(doc)) == "Good evening, sir."


def test_tool_call_step_streams_nothing():
    s = _FinalStreamer()
    assert s.feed('{"tool": "news", "params": {"topic": "technology"}}') == ""


def test_emits_only_newly_revealed_characters():
    s = _FinalStreamer()
    assert s.feed('{"final": "Hel') == "Hel"
    assert s.feed("lo") == "lo"
    assert s.feed(' there."}') == " there."


def test_decodes_escapes():
    doc = r'{"final": "He said \"hi\".\nDone."}'
    assert feed_all([doc]) == 'He said "hi".\nDone.'


def test_dangling_escape_waits_for_next_delta():
    s = _FinalStreamer()
    assert s.feed('{"final": "tab\\') == "tab"   # backslash split across deltas → hold it
    assert s.feed('tdone"}') == "\tdone"


def test_literal_unicode_passes_through():
    s = _FinalStreamer()
    assert s.feed('{"final": "café ☕"}') == "café ☕"


def test_stops_at_closing_quote_ignores_trailing_json():
    # Text after the closing quote (other keys, the closing brace) must not leak into the answer.
    s = _FinalStreamer()
    assert s.feed('{"final": "Done.", "data": {"x": 1}}') == "Done."

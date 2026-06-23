"""Tests for the TTS text-normalisation helpers — pure string functions that make a reply
safe for the phonemizer without losing meaning. The actual Kokoro synthesis is heavy and
loaded lazily, so it isn't exercised here; only the sanitisers are.
"""
from app.tts import MAX_CHUNK_CHARS, _ascii_fallback, _chunk, _speakable


def test_speakable_passes_plain_text_through():
    assert _speakable("Hello, world.") == "Hello, world."


def test_speakable_empty():
    assert _speakable("") == ""


def test_urls_become_spoken_phrase():
    assert "a link" in _speakable("see https://example.com/x for details")
    assert "http" not in _speakable("see https://example.com/x")


def test_hashtags_and_handles():
    assert "hashtag lofi" in _speakable("playing #lofi")
    assert "at jack" in _speakable("message @jack now")


def test_ampersand_becomes_and():
    assert _speakable("rock & roll") == "rock and roll"


def test_emoji_stripped():
    out = _speakable("nice 🎉 work 🚀")
    assert "🎉" not in out and "🚀" not in out
    assert "nice" in out and "work" in out


def test_smart_quotes_normalised():
    assert "'" in _speakable("‘single’")
    assert '"' in _speakable("“double”")


def test_ascii_fallback_drops_accents():
    assert _ascii_fallback("Beyoncé") == "Beyonce"
    assert _ascii_fallback("café résumé") == "cafe resume"


def test_chunk_keeps_short_text_in_one_piece():
    chunks = _chunk("Short reply.")
    assert chunks == ["Short reply."]


def test_chunk_splits_long_text_within_budget():
    text = " ".join(["This is sentence number %d." % i for i in range(80)])
    chunks = _chunk(text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHUNK_CHARS for c in chunks)
    # No content is dropped: every sentence's number still appears somewhere.
    joined = " ".join(chunks)
    assert "number 0." in joined and "number 79." in joined

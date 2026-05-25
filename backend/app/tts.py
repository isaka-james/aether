"""Text-to-speech using Kokoro (ONNX, CPU) with the 'sky' voice.

Kokoro degrades on long inputs — it batches at arbitrary phoneme cuts and the joins
come out clipped or run-together. So for anything beyond a sentence or two we split
the text at natural sentence boundaries ourselves, synthesize each chunk, and
concatenate the audio with a short silence between chunks.

Produces 16-bit PCM WAV bytes at the model's native 24kHz sample rate, suitable both
for playback on the host (via the host agent) and for the browser.
"""
import io
import logging
import re

import numpy as np
import soundfile as sf

from .config import get_settings

log = logging.getLogger("aether.tts")
_kokoro = None

# Keep each synthesis chunk well under Kokoro's ~510-phoneme limit. Characters are a
# rough proxy for phonemes, so we stay conservative to leave headroom.
MAX_CHUNK_CHARS = 280
# Short pause inserted between chunks so concatenated sentences don't run together.
GAP_SECONDS = 0.18

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*\s*")


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro  # imported lazily; heavy
        s = get_settings()
        log.info("Loading Kokoro TTS (voice=%s)...", s.kokoro_voice)
        _kokoro = Kokoro(s.kokoro_onnx_path, s.kokoro_voices_path)
    return _kokoro


def _chunk(text: str) -> list[str]:
    """Split text into chunks at sentence boundaries, each <= MAX_CHUNK_CHARS.
    A single sentence longer than the budget is hard-wrapped on whitespace."""
    chunks: list[str] = []
    cur = ""
    for piece in _SENTENCE.findall(text) or [text]:
        sent = piece.strip()
        if not sent:
            continue
        if len(sent) > MAX_CHUNK_CHARS:          # oversize sentence -> wrap on words
            if cur:
                chunks.append(cur)
                cur = ""
            line = ""
            for w in sent.split():
                if len(line) + len(w) + 1 > MAX_CHUNK_CHARS:
                    chunks.append(line)
                    line = w
                else:
                    line = f"{line} {w}".strip()
            cur = line
            continue
        if len(cur) + len(sent) + 1 > MAX_CHUNK_CHARS:
            chunks.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def synthesize(text: str) -> bytes:
    """Render `text` to WAV bytes, concatenating per-sentence audio. Returns b'' on
    empty input."""
    text = (text or "").strip()
    if not text:
        return b""
    s = get_settings()
    kokoro = _get_kokoro()

    sample_rate = 24000
    parts: list[np.ndarray] = []
    for chunk in _chunk(text):
        samples, sample_rate = kokoro.create(
            chunk, voice=s.kokoro_voice, speed=s.kokoro_speed, lang=s.kokoro_lang
        )
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size == 0:
            continue
        if parts:  # silence gap between chunks
            parts.append(np.zeros(int(sample_rate * GAP_SECONDS), dtype=np.float32))
        parts.append(samples)

    if not parts:
        return b""
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(parts), sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()
